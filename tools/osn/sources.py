from __future__ import annotations

import ipaddress
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import detect, parse
from .store import OsnError, PolicyError, Project, atomic_write, load_toml, now, sha256, write_json

KINDS = {"primary", "secondary", "reviews", "own", "scenario", "interview", "brief"}
MAX_BYTES = 8_000_000


def allowed_domains(project: Project) -> set[str]:
    base = set(load_toml("allowlist.toml").get("domains", []))
    approved = set(project.meta().get("approved_domains", []))
    return base | approved


def domain_allowed(host: str, domains: set[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def _assert_public(host: str) -> None:
    for *_, sockaddr in socket.getaddrinfo(host, None):
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise PolicyError(f"Адрес {host} ведёт во внутреннюю сеть, загрузка запрещена.")


def _download(url: str, retries: int = 2) -> tuple[bytes, str]:
    last = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "osnovanie/0.1 (research agent)"})
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise OsnError("Файл больше 8 МБ, загрузка прервана.")
                return body, response.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last = error
            if isinstance(error, urllib.error.HTTPError) and error.code < 500:
                break
            time.sleep(1.5 * (attempt + 1))
    raise OsnError(f"Не удалось загрузить {url}: {last}")


SUFFIX_BY_TYPE = {
    "text/html": ".html", "application/json": ".json", "text/csv": ".csv", "text/plain": ".txt",
    "application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
}


def plan(project: Project, url: str, why: str, kind: str) -> dict:
    with project.lock():
        known = [r for r in project.records("source_plan") if r["url"] == url]
        if known:
            return known[-1]
        record = {"id": project.next_id("source_plan", "p"), "url": url, "why": why, "kind": kind, "ts": now()}
        project.append("source_plan", record)
        project.event("source_plan", url=url)
        return record


def ingest_bytes(project: Project, raw: bytes, suffix: str, title: str, kind: str, origin: str, published: str | None) -> dict:
    if kind not in KINDS:
        raise OsnError(f"Тип источника: {', '.join(sorted(KINDS))}.")
    digest = sha256(raw)
    with project.lock():
        for record in project.current("sources").values():
            if record["sha256"] == digest:
                return {**record, "duplicate": True}
        source_id = project.next_id("sources", "s")
        raw_path = project.raw / f"{source_id}{suffix}"
        atomic_write(raw_path, raw)
        try:
            result = parse.parse_file(raw_path)
        except OsnError:
            raw_path.unlink(missing_ok=True)
            raise
        clean, quarantined = _quarantine(project, source_id, result["text"], result.get("hidden", []))
        atomic_write(project.parsed / f"{source_id}.txt", clean)
        record = {
            "id": source_id, "title": title, "kind": kind, "origin": origin, "sha256": digest,
            "raw": str(raw_path.relative_to(project.base)), "parsed": f"materials/parsed/{source_id}.txt",
            "accessed_at": now(), "published_at": published, "parser": result["parser"], "chars": len(clean),
            "quarantined": quarantined, "status": "active",
            **{k: result[k] for k in ("width", "height", "rows") if k in result},
        }
        project.append("sources", record)
        project.event("source_added", id=source_id, title=title, quarantined=quarantined)
        return record


def _quarantine(project: Project, source_id: str, text: str, hidden: list[str]) -> tuple[str, int]:
    clean_parts, count = [], 0
    log_records = []
    for index, segment in enumerate(detect.segments(text)):
        verdict = detect.scan(segment)
        if verdict["suspicious"]:
            count += 1
            q_id = f"q-{source_id}-{index}"
            log_records.append({"id": q_id, "source_id": source_id, "text": segment[:2000], "hits": verdict["strong"], "flags": verdict["flags"], "where": "visible"})
            clean_parts.append(f"[КАРАНТИН {q_id}: фрагмент похож на команду и скрыт]")
        else:
            clean_parts.append(detect.ZERO_WIDTH.sub("", segment).rstrip("\n"))
    for index, segment in enumerate(hidden):
        verdict = detect.scan(segment)
        if verdict["suspicious"]:
            count += 1
            log_records.append({"id": f"q-{source_id}-h{index}", "source_id": source_id, "text": segment[:2000], "hits": verdict["strong"], "flags": verdict["flags"], "where": "hidden"})
    for record in log_records:
        project.append("injection_log", {**record, "ts": now(), "status": "quarantined"})
    if log_records:
        atomic_write(project.quarantine / f"{source_id}.txt", "\n---\n".join(r["text"] for r in log_records))
    return "\n".join(clean_parts) + "\n", count


def add_file(project: Project, path: Path, title: str | None, kind: str, published: str | None) -> dict:
    if not path.exists():
        raise OsnError(f"Файл не найден: {path}")
    return ingest_bytes(project, path.read_bytes(), path.suffix.lower(), title or path.name, kind, f"file:{path.name}", published)


def fetch(project: Project, url: str, title: str | None, kind: str, published: str | None) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PolicyError("Разрешены только адреса http и https.")
    if not domain_allowed(parsed.hostname, allowed_domains(project)):
        raise PolicyError(
            f"Домен {parsed.hostname} не разрешён. Предложите его в плане источников (osnovanie source plan), "
            "человек подтвердит в точке G2."
        )
    _assert_public(parsed.hostname)
    for record in project.current("sources").values():
        if record["origin"] == url and record["status"] == "active":
            return {**record, "duplicate": True}
    try:
        body, content_type = _download(url)
    except OsnError as error:
        project.append("failures", {"ts": now(), "op": "fetch", "url": url, "error": str(error)})
        project.event("failure", op="fetch", url=url)
        raise
    suffix = SUFFIX_BY_TYPE.get(content_type) or Path(parsed.path).suffix.lower() or ".html"
    return ingest_bytes(project, body, suffix, title or url, kind, url, published)


def parsed_text(project: Project, source_id: str) -> str:
    path = project.parsed / f"{source_id}.txt"
    if not path.exists():
        raise OsnError(f"Источник {source_id} не найден.")
    return path.read_text("utf-8")


def save_source_note(project: Project, source_id: str, note: dict) -> None:
    write_json(project.state / "notes" / f"{source_id}.json", note)
