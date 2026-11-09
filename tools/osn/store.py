from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

MSK = timezone(timedelta(hours=3))


class OsnError(Exception):
    """Отказ, который пользователь или агент должен увидеть как есть."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


class PolicyError(OsnError):
    def __init__(self, message: str):
        super().__init__(message, code=2)


def repo_root() -> Path:
    env = os.environ.get("OSNOVANIE_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(MSK).isoformat(timespec="seconds")


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def write_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text("utf-8"))


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        # После обрыва записи хвост может быть без перевода строки. Новая запись
        # не должна склеиться с ним, иначе потеряются обе.
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            line = "\n" + line
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    return scan_jsonl(path)[0]


def scan_jsonl(path: Path) -> tuple[list[dict], list[tuple[int, str]]]:
    if not path.exists():
        return [], []
    records, broken = [], []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            broken.append((number, line))
    return records, broken


def latest_by_id(records: list[dict], key: str = "id") -> dict[str, dict]:
    result: dict[str, dict] = {}
    for record in records:
        result[record[key]] = record
    return result


SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


class Project:
    def __init__(self, slug: str, root: Path | None = None):
        self.root = root or repo_root()
        self.slug = slug
        self.base = self.root / "projects" / slug
        self.raw = self.base / "materials" / "raw"
        self.parsed = self.base / "materials" / "parsed"
        self.quarantine = self.base / "materials" / "quarantine"
        self.state = self.base / "state"
        self.outputs = self.base / "outputs"
        self.cache = self.state / "cache"

    def exists(self) -> bool:
        return (self.base / "project.json").exists()

    def meta(self) -> dict:
        return read_json(self.base / "project.json", {})

    def save_meta(self, meta: dict) -> None:
        write_json(self.base / "project.json", meta)

    def log(self, name: str) -> Path:
        return self.state / f"{name}.jsonl"

    def records(self, name: str) -> list[dict]:
        return read_jsonl(self.log(name))

    def current(self, name: str) -> dict[str, dict]:
        return latest_by_id(self.records(name))

    def append(self, name: str, record: dict) -> None:
        append_jsonl(self.log(name), record)

    def event(self, kind: str, **data) -> None:
        self.append("events", {"ts": now(), "kind": kind, **data})

    @contextlib.contextmanager
    def lock(self):
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / ".lock", "w") as handle:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def next_id(self, name: str, prefix: str, width: int = 3) -> str:
        numbers = [
            int(r["id"][len(prefix):])
            for r in self.records(name)
            if r["id"].startswith(prefix) and r["id"][len(prefix):].isdigit()
        ]
        return f"{prefix}{(max(numbers) if numbers else 0) + 1:0{width}d}"


def active_file(root: Path | None = None) -> Path:
    return (root or repo_root()) / "projects" / ".active"


def active_project(slug: str | None = None, required: bool = True) -> Project | None:
    root = repo_root()
    name = slug or os.environ.get("OSNOVANIE_PROJECT")
    if not name and active_file(root).exists():
        name = active_file(root).read_text("utf-8").strip()
    if name:
        project = Project(name, root)
        if project.exists():
            return project
        if required:
            raise OsnError(f"Проект «{name}» не найден. Список: osnovanie list")
    if required:
        raise OsnError('Нет активного проекта. Начните: osnovanie new <имя> --question "..."')
    return None


def load_toml(name: str) -> dict:
    import tomllib

    path = repo_root() / "config" / name
    with open(path, "rb") as fh:
        return tomllib.load(fh)
