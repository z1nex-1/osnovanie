from __future__ import annotations

import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

from .store import OsnError, PolicyError, Project, now, repo_root, sha256

SECRET = re.compile(r"sk-ant-[\w-]{10,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_-]?key|token|password)\s*[=:]\s*['\"][^'\"\s]{12,}")
SKIP = {"state/index", "state/cache", "state/.lock"}
AGENT_PATHS = ["AGENTS.md", "CLAUDE.md", "README.md", ".claude", ".agents", ".cursor", ".codex", ".mcp.json", "bin", "tools", "config", "instructions", "methods", "kb"]


def _skip(relative: str) -> bool:
    return any(relative == s or relative.startswith(s + "/") for s in SKIP) or "__pycache__" in relative


def _files(project: Project, with_agent: bool):
    for path in sorted(project.base.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(project.base).as_posix()
            if not _skip(relative):
                yield path, f"project/{relative}"
    if with_agent:
        root = repo_root()
        for name in AGENT_PATHS:
            target = root / name
            items = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file() and "__pycache__" not in p.parts) if target.exists() else []
            for path in items:
                yield path, f"agent/{path.relative_to(root).as_posix()}"


def export(project: Project, destination: Path, with_agent: bool = False) -> dict:
    manifest: dict[str, str] = {}
    buffer: list[tuple[str, bytes]] = []
    for path, name in _files(project, with_agent):
        data = path.read_bytes()
        if SECRET.search(data.decode("utf-8", "ignore")):
            raise PolicyError(f"В {name} похоже на секрет, экспорт остановлен.")
        manifest[name] = sha256(data)
        buffer.append((name, data))
    head = json.dumps({"format": "osnovanie-1", "slug": project.slug, "ts": now(), "files": manifest}, ensure_ascii=False, indent=1).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        for name, data in [("manifest.json", head), *buffer]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return {"archive": str(destination), "files": len(manifest), "sha256": sha256(destination.read_bytes())}


def _safe(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PolicyError(f"В архиве небезопасный путь: {name}")
    return path


def import_archive(source: Path, target_slug: str | None = None, force: bool = False) -> dict:
    if not source.exists():
        raise OsnError(f"Архив не найден: {source}")
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            _safe(member.name)
            if not member.isfile():
                raise PolicyError(f"В архиве не обычный файл: {member.name}")
        manifest = json.load(archive.extractfile("manifest.json"))
        slug = target_slug or manifest["slug"]
        project = Project(slug)
        if project.exists() and not force:
            raise PolicyError(f"Проект {slug} уже есть. Укажите другое имя или --force.")
        payload = {}
        for member in members:
            if member.name == "manifest.json":
                continue
            data = archive.extractfile(member).read()
            if manifest["files"].get(member.name) != sha256(data):
                raise PolicyError(f"Хеш файла {member.name} не совпадает с манифестом.")
            payload[member.name] = data
    missing = set(manifest["files"]) - set(payload)
    if missing:
        raise PolicyError(f"В архиве нет файлов: {', '.join(sorted(missing)[:5])}")
    written = 0
    for name, data in payload.items():
        path = _safe(name)
        if path.parts[0] != "project":
            continue
        out = project.base.joinpath(*path.parts[1:])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        written += 1
    project.event("imported", files=written, origin=manifest["ts"])
    return {"project": slug, "files": written}
