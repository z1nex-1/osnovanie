"""Сторож источников: перескачивает страницы и сверяет их с тем, на что опираются утверждения."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import detect, parse, review, sources, verify
from .claims import update as update_claim
from .store import OsnError, PolicyError, Project, now, sha256

UNCHANGED, CHANGED, VANISHED, UNREACHABLE = "без изменений", "страница изменилась", "цитата исчезла", "недоступно"
SPLIT = re.compile(r"\s*(?:\.\.\.|…)\s*")


def sanitize(text: str) -> tuple[str, int]:
    """Та же защита, что при загрузке: фрагменты, похожие на команды, в сравнение не попадают."""
    kept, dropped = [], 0
    for segment in detect.segments(text):
        if detect.scan(segment)["suspicious"]:
            dropped += 1
            continue
        kept.append(detect.ZERO_WIDTH.sub("", segment).strip())
    return "\n".join(kept), dropped


def page_hash(text: str) -> str:
    return sha256(verify.normalize(text))


def _load(project: Project, url: str) -> tuple[str, int]:
    parsed = sources.check_url(project, url)
    body, content_type = sources._download(url)
    suffix = sources.SUFFIX_BY_TYPE.get(content_type) or Path(parsed.path).suffix.lower() or ".html"
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"page{suffix}"
        path.write_bytes(body)
        text = parse.parse_file(path)["text"]
    return sanitize(text)


def quote_present(quote: str, page_flat: str) -> bool:
    parts = [p for p in SPLIT.split(quote) if len(verify.normalize(p)) >= 4]
    return bool(parts) and all(verify._flat(p) in page_flat for p in parts)


def _baseline(project: Project, source_id: str) -> str | None:
    seen = [r for r in project.records("watch") if r["source_id"] == source_id and r["status"] != UNREACHABLE]
    return seen[-1]["hash"] if seen else None


def run(project: Project, apply: bool = False) -> dict:
    claims = project.current("claims")
    rows, vanished = [], []
    for source in sorted(project.current("sources").values(), key=lambda s: s["id"]):
        url = source.get("origin", "")
        if not url.startswith(("http://", "https://")) or source.get("status") != "active":
            continue
        quoted = [c for c in claims.values() if c.get("source_id") == source["id"] and c.get("quote") and c["status"] == "verified" and not c.get("superseded_by")]
        row = {"source_id": source["id"], "url": url, "title": source["title"], "checked": [c["id"] for c in quoted], "missing": [], "dropped_segments": 0, "hash": None, "ts": now()}
        try:
            text, row["dropped_segments"] = _load(project, url)
        except (OsnError, PolicyError, OSError) as error:
            row.update(status=UNREACHABLE, reason=str(error)[:200])
            rows.append(row)
            continue
        row["hash"] = page_hash(text)
        flat = verify._flat(text)
        row["missing"] = [c["id"] for c in quoted if not quote_present(c["quote"], flat)]
        previous = _baseline(project, source["id"])
        row["previous_hash"] = previous
        row["baseline"] = previous is None
        if row["missing"]:
            row["status"] = VANISHED
        elif previous is not None and previous != row["hash"]:
            row["status"] = CHANGED
        else:
            row["status"] = UNCHANGED
        rows.append(row)
    for row in rows:
        row["id"] = project.next_id("watch", "w")
        project.append("watch", row)
        vanished += row["missing"]
    summary = {status: sum(1 for r in rows if r["status"] == status) for status in (UNCHANGED, CHANGED, VANISHED, UNREACHABLE)}
    result = {"checked": len(rows), "summary": summary, "sources": rows, "applied": False, "preview": None}
    if apply and vanished:
        marked = sorted(set(vanished))
        for claim_id in marked:
            update_claim(project, claim_id, status="stale", stale_reason="сторож: цитата исчезла со страницы источника", stale_at=now())
        result.update(applied=True, marked=marked, preview=review.preview(project, set(marked)))
    project.event("watch", checked=len(rows), summary=summary, marked=result.get("marked", []))
    return result


def render(result: dict) -> str:
    lines = [f"Сторож: проверено источников {result['checked']}. " + ", ".join(f"{k}: {v}" for k, v in result["summary"].items())]
    for row in result["sources"]:
        mark = {UNCHANGED: "ok", CHANGED: "!", VANISHED: "!!", UNREACHABLE: "?"}[row["status"]]
        extra = f" пропали цитаты в {', '.join(row['missing'])}" if row["missing"] else ""
        lines.append(f"  [{mark}] {row['source_id']} {row['status']}{extra} — {row['url'][:70]}")
    if result["applied"]:
        lines.append(f"Помечены устаревшими: {', '.join(result['marked'])}. Дальше: osnovanie review run.")
    elif result["summary"][VANISHED]:
        lines.append("Чтобы пометить затронутые утверждения: osnovanie watch --apply")
    return "\n".join(lines)
