from __future__ import annotations

import json
import sys

from . import claims as claims_mod, cost, model, review, search, verify, workflow
from .store import OsnError, active_project


def _project(args: dict):
    return active_project(args.get("project"))


def kb_status(a):
    from .cli import status_data

    return status_data(_project(a))


def kb_resume(a):
    from .cli import resume_text

    return {"text": resume_text(_project(a))}


def claims_search(a):
    return search.search(_project(a), a["query"], a.get("mode", "hybrid"), a.get("limit", 8))


def claim_get(a):
    return claims_mod.get(_project(a), a["id"])


def claim_add(a):
    return claims_mod.add(_project(a), a["claim"], author="agent:mcp")


def claim_supersede(a):
    return claims_mod.supersede(_project(a), a["id"], a["claim"])


def verify_claims(a):
    return verify.run(_project(a))


def options_list(a):
    return list(_project(a).current("options").values())


def model_run(a):
    return model.run(_project(a), tag="mcp")


def review_preview(a):
    return review.preview(_project(a))


def cost_report(a):
    return cost.summary(_project(a))


def gate_status(a):
    project = _project(a)
    state = workflow.load(project)
    return {"awaiting": state["awaiting"], "gates": state["gates"]}


PROJECT = {"project": {"type": "string", "description": "имя проекта, по умолчанию активный"}}
CLAIM = {
    "type": "object", "required": ["text", "type"],
    "properties": {"text": {"type": "string"}, "type": {"type": "string", "enum": list(claims_mod.TYPES)}, "source_id": {"type": "string"}, "quote": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"}, "period": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}}},
}
TOOLS = {
    "kb_status": (kb_status, "Текущий этап, точки человека, счётчики и бюджет.", {}),
    "kb_resume": (kb_resume, "Сводка для продолжения работы в новом чате.", {}),
    "claims_search": (claims_search, "Поиск утверждений по формулировке, смыслу и связям.", {"query": {"type": "string"}, "mode": {"type": "string", "enum": ["hybrid", "word", "sense", "links"]}, "limit": {"type": "integer"}}),
    "claim_get": (claim_get, "Одно утверждение со статусом, источником и цитатой.", {"id": {"type": "string"}}),
    "claim_add": (claim_add, "Записать утверждение. Факт без дословной цитаты отклоняется.", {"claim": CLAIM}),
    "claim_supersede": (claim_supersede, "Заменить утверждение новым; старое остаётся в журнале.", {"id": {"type": "string"}, "claim": CLAIM}),
    "verify_claims": (verify_claims, "Проверить дословность цитат и числа.", {}),
    "options_list": (options_list, "Варианты с возражениями.", {}),
    "model_run": (model_run, "Пересчитать модель вариантов.", {}),
    "review_preview": (review_preview, "Что затронут изменённые утверждения, без записи.", {}),
    "cost_report": (cost_report, "Стоимость проекта.", {}),
    "gate_status": (gate_status, "Состояние точек человека. Подтвердить точку через MCP нельзя.", {}),
}
REQUIRED = {"claims_search": ["query"], "claim_get": ["id"], "claim_add": ["claim"], "claim_supersede": ["id", "claim"]}


def listing() -> list[dict]:
    return [{"name": name, "description": text, "inputSchema": {"type": "object", "properties": {**PROJECT, **props}, "required": REQUIRED.get(name, [])}} for name, (_, text, props) in TOOLS.items()]


def handle(message: dict) -> dict | None:
    method, rid = message.get("method"), message.get("id")
    if rid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "kb-state", "version": "1.0.0"}}
    elif method == "tools/list":
        result = {"tools": listing()}
    elif method == "ping":
        result = {}
    elif method == "tools/call":
        params = message.get("params", {})
        name = params.get("name")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"Неизвестный инструмент {name}"}}
        try:
            payload = TOOLS[name][0](params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}], "isError": False}
        except (OsnError, KeyError, TypeError, ValueError) as error:
            result = {"content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Метод не поддерживается"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Неверный JSON"}}
        if reply:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
