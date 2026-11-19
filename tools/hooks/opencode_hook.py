#!/usr/bin/env python3
"""Адаптер событий opencode. Плагин .opencode/plugins/osnovanie.js передаёт сюда вызовы инструментов и сообщения человека."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from osn import cli, guard, workflow  # noqa: E402
from osn.store import active_project  # noqa: E402

AGENT = "opencode-main"
# opencode называет инструменты по-своему; политика одна, поэтому имена приводятся к именам Claude Code
TOOLS = {"bash": "Bash", "read": "Read", "write": "Write", "edit": "Edit", "patch": "Edit", "multiedit": "MultiEdit",
         "glob": "Glob", "grep": "Grep", "list": "Glob", "webfetch": "WebFetch", "websearch": "WebSearch",
         "todowrite": "TodoWrite", "todoread": "TodoWrite", "task": "Task", "skill": "Skill"}


def tool_input(tool: str, args: dict) -> dict:
    path = args.get("filePath") or args.get("file_path") or args.get("path")
    if tool == "Bash":
        return {"command": args.get("command", "")}
    if tool in {"Grep", "Glob"}:
        return {"path": path}
    return {"file_path": path or ""}


def handle(event: str, payload: dict) -> dict:
    if event == "tool":
        raw = str(payload.get("tool", ""))
        name = TOOLS.get(raw, raw)
        if name == "Task":
            return {"allow": False, "reason": "Субагентов в этой среде нет: работайте сами по навыку этапа."}
        verdict = guard.decide(name, tool_input(name, payload.get("args") or {}), AGENT)
        project = active_project(required=False)
        if not verdict["allow"] and project:
            project.event("blocked", tool=name, agent=AGENT, reason=verdict["reason"][:200])
        return verdict
    project = active_project(required=False)
    if event == "start":
        return {"context": cli.resume_text(project) if project else "Проекта пока нет: bin/osnovanie new <имя> --question \"...\"."}
    if event == "prompt" and project:
        reply = workflow.handle_human_prompt(project, payload.get("prompt", ""), "opencode")
        if reply:
            cli.write_progress(project)
        return {"context": reply or ""}
    if event == "idle" and project:
        cli.write_progress(project)
    return {}


if __name__ == "__main__":
    try:
        print(json.dumps(handle(sys.argv[1] if len(sys.argv) > 1 else "", json.load(sys.stdin)), ensure_ascii=False))
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"allow": False, "reason": f"Сбой политики агента: {error}"}, ensure_ascii=False))
