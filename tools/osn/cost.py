from __future__ import annotations

import json
from pathlib import Path

from . import workflow
from .store import Project, load_toml, now, repo_root


def price_table() -> dict:
    return load_toml("prices.toml")


def _rates(model: str, table: dict) -> dict | None:
    models = table["models"]
    if model in models:
        return models[model]
    candidates = [name for name in models if model.startswith(name)]
    return models[max(candidates, key=len)] if candidates else None


def usd(model: str, usage: dict, table: dict | None = None) -> float:
    table = table or price_table()
    rates = _rates(model, table)
    if rates is None:
        return 0.0
    amount = (
        usage.get("input_tokens", 0) * rates["input"]
        + usage.get("output_tokens", 0) * rates["output"]
        + usage.get("cache_read_input_tokens", 0) * rates["cache_read"]
        + usage.get("cache_creation_input_tokens", 0) * rates["cache_write"]
    ) / 1_000_000
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
    return amount + searches * table.get("web_search_usd", 0)


def _rework(project: Project) -> bool:
    return any(g.get("round", 0) > 1 for g in workflow.load(project)["gates"].values())


def ingest_transcript(project: Project, transcript: Path, agent: str = "main") -> int:
    if not transcript.exists():
        return 0
    known = {r["message_id"] for r in project.records("costs") if r.get("message_id")}
    table = price_table()
    latest: dict[str, dict] = {}
    for line in transcript.read_text("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") or {}
        if entry.get("type") == "assistant" and message.get("usage") and message.get("id"):
            latest[message["id"]] = message
    stage = workflow.load(project)["stage"]
    added = 0
    rework = _rework(project)
    for message_id, message in latest.items():
        if message_id in known or message.get("model") == "<synthetic>":
            continue
        usage = message["usage"]
        project.append("costs", {
            "ts": now(), "kind": "llm", "agent": agent, "model": message.get("model"), "message_id": message_id, "stage": stage,
            "input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0), "cache_write": usage.get("cache_creation_input_tokens", 0),
            "usd": round(usd(message.get("model", ""), usage, table), 6), "rework": rework,
        })
        added += 1
    return added


def ingest_session_dir(project: Project, transcript: Path) -> int:
    added = ingest_transcript(project, transcript)
    sub = transcript.with_suffix("") / "subagents"
    if sub.is_dir():
        for path in sorted(sub.glob("*.jsonl")):
            meta_path = path.with_suffix(".meta.json")
            agent = "subagent"
            if meta_path.exists():
                try:
                    agent = json.loads(meta_path.read_text("utf-8")).get("agentType", agent)
                except json.JSONDecodeError:
                    pass
            added += ingest_transcript(project, path, agent)
    return added


def record_tool(project: Project, kind: str, model: str, usd_amount: float, note: str, tokens: int = 0) -> None:
    project.append("costs", {"ts": now(), "kind": kind, "agent": "tool", "model": model, "stage": workflow.load(project)["stage"], "input": tokens, "output": 0, "cache_read": 0, "cache_write": 0, "usd": round(usd_amount, 6), "note": note})


def add_human(project: Project, minutes: float, note: str) -> None:
    project.append("costs", {"ts": now(), "kind": "human", "agent": "human", "model": None, "stage": workflow.load(project)["stage"], "minutes": minutes, "usd": 0.0, "note": note})
    project.event("human_time", minutes=minutes, note=note)


def summary(project: Project) -> dict:
    records = project.records("costs")
    llm = [r for r in records if r["kind"] != "human"]
    human = [r for r in records if r["kind"] == "human"]
    table = price_table()
    spent = sum(r["usd"] for r in llm)
    minutes = sum(r.get("minutes", 0) for r in human)
    human_rub = minutes / 60 * table["human_rub_per_hour"]
    def group(field: str) -> dict:
        out: dict[str, dict] = {}
        for record in llm:
            key = str(record.get(field) or "—")
            item = out.setdefault(key, {"usd": 0.0, "calls": 0, "tokens": 0})
            item["usd"] += record["usd"]
            item["calls"] += 1
            item["tokens"] += record.get("input", 0) + record.get("output", 0) + record.get("cache_read", 0) + record.get("cache_write", 0)
        return {k: {**v, "usd": round(v["usd"], 4)} for k, v in sorted(out.items(), key=lambda kv: -kv[1]["usd"])}
    events = project.records("events")
    return {
        "llm_usd": round(spent, 4), "llm_rub": round(spent * table["usd_rub"], 2), "calls": len(llm),
        "human_minutes": minutes, "human_rub": round(human_rub, 2),
        "total_rub": round(spent * table["usd_rub"] + human_rub, 2),
        "rework_usd": round(sum(r["usd"] for r in llm if r.get("rework")), 4),
        "failures": len(project.records("failures")),
        "by_stage": group("stage"), "by_model": group("model"), "by_agent": group("agent"), "by_kind": group("kind"),
        "budget_usd": project.meta().get("budget_usd"),
        "elapsed": {"first": events[0]["ts"], "last": events[-1]["ts"]} if events else None,
        "assumptions": {"usd_rub": table["usd_rub"], "human_rub_per_hour": table["human_rub_per_hour"], "prices_checked": table["checked_at"]},
    }


def over_budget(project: Project) -> bool:
    budget = project.meta().get("budget_usd")
    return bool(budget) and summary(project)["llm_usd"] >= budget


def report_md(project: Project) -> str:
    data = summary(project)
    lines = [f"# Стоимость проекта «{project.slug}»", ""]
    lines.append(f"Модели и инструменты: **${data['llm_usd']:.2f}** ({data['llm_rub']:.0f} ₽), {data['calls']} вызовов. Бюджет: ${data['budget_usd'] or 0:.2f}.")
    lines.append(f"Время человека: {data['human_minutes']:.0f} мин ({data['human_rub']:.0f} ₽ по ставке-допущению). Полная стоимость: **{data['total_rub']:.0f} ₽**.")
    lines.append(f"Повторы и доработка по правкам: ${data['rework_usd']:.2f}. Зафиксированных сбоев инструментов: {data['failures']}.")
    for title, key in (("По этапам", "by_stage"), ("По моделям", "by_model"), ("По исполнителям", "by_agent")):
        lines += ["", f"## {title}", "", "| | USD | Вызовы | Токены |", "|---|---:|---:|---:|"]
        lines += [f"| {k} | {v['usd']:.4f} | {v['calls']} | {v['tokens']} |" for k, v in data[key].items()]
    lines += ["", f"Допущения: курс {data['assumptions']['usd_rub']} ₽/$, час человека {data['assumptions']['human_rub_per_hour']:.0f} ₽, цены на {data['assumptions']['prices_checked']}.", f"Файл цен: `{(repo_root() / 'config/prices.toml').relative_to(repo_root())}`."]
    return "\n".join(lines) + "\n"
