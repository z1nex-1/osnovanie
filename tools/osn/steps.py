from __future__ import annotations

from .store import OsnError, Project, now, sha256


def _digest(key: str, payload: str) -> str:
    return sha256(f"{key}\0{payload}")[:16]


def state_of(project: Project, key: str, payload: str = "") -> dict:
    digest = _digest(key, payload)
    done = begun = None
    for record in project.records("steps"):
        if record["digest"] == digest:
            if record["state"] == "done":
                done = record
            else:
                begun = record
    if done:
        return {"status": "done", "result": done.get("result"), "at": done["ts"]}
    if begun:
        return {"status": "interrupted", "at": begun["ts"]}
    return {"status": "new"}


def begin(project: Project, key: str, payload: str = "") -> dict:
    with project.lock():
        current = state_of(project, key, payload)
        if current["status"] == "new":
            project.append("steps", {"ts": now(), "key": key, "digest": _digest(key, payload), "state": "begun"})
        return current


def done(project: Project, key: str, payload: str = "", result=None) -> None:
    with project.lock():
        if state_of(project, key, payload)["status"] == "new":
            raise OsnError(f"Шаг {key} не был начат.")
        project.append("steps", {"ts": now(), "key": key, "digest": _digest(key, payload), "state": "done", "result": result})


def unfinished(project: Project) -> list[str]:
    finished = {r["digest"] for r in project.records("steps") if r["state"] == "done"}
    return sorted({r["key"] for r in project.records("steps") if r["state"] == "begun" and r["digest"] not in finished})
