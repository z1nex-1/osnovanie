#!/usr/bin/env python3
"""Адаптер событий Cursor. В Cursor нет разделения прав между субагентами, поэтому агент получает роль cursor-main."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from osn import cli, guard, workflow  # noqa: E402
from osn.store import active_project  # noqa: E402

AGENT = "cursor-main"


def permission(verdict: dict) -> dict:
    if verdict["allow"]:
        return {"permission": "allow"}
    return {"permission": "deny", "user_message": verdict["reason"], "agent_message": verdict["reason"]}


def handle(event: str, payload: dict) -> dict:
    if event == "beforeShellExecution":
        return permission(guard.check_bash(payload.get("command", ""), AGENT))
    if event == "beforeReadFile":
        return permission(guard.check_read(payload.get("file_path", ""), AGENT))
    if event == "preToolUse":
        tool = payload.get("tool_name", "")
        tool_input = payload.get("tool_input") or {}
        if tool in {"Write", "Edit", "MultiEdit", "StrReplace", "Delete"}:
            return permission(guard.check_write(tool_input.get("file_path") or tool_input.get("path", ""), AGENT))
        if tool == "WebFetch":
            return permission(guard.decide("WebFetch", tool_input, AGENT))
        return {"permission": "allow"}
    project = active_project(required=False)
    if event == "sessionStart":
        return {"additional_context": cli.resume_text(project) if project else "Проекта пока нет: bin/osnovanie new <имя> --question \"...\"."}
    if event == "beforeSubmitPrompt" and project:
        reply = workflow.handle_human_prompt(project, payload.get("prompt", ""), "chat")
        if reply:
            cli.write_progress(project)
        return {"continue": True}
    if event in {"stop", "afterFileEdit"} and project:
        cli.write_progress(project)
    return {}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        print(json.dumps(handle(name, json.load(sys.stdin)), ensure_ascii=False))
    except Exception as error:  # noqa: BLE001
        print(f"Сбой политики агента: {error}", file=sys.stderr)
        sys.exit(2)
