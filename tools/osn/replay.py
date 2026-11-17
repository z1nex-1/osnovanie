"""Журналы Claude Code и состояние проекта → реплей прогона (схема osnovanie-replay/1).

Реплей показывает, как агент пришёл к стратегии: реплики, вызовы инструментов, утверждения,
точки человека, деньги. Рассуждения модели (thinking), системные вставки и тексты из
карантина в реплей не попадают. Если после вычистки в файле остался похожий на секрет
фрагмент, файл не пишется.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import claims as claims_mod, cost
from .store import OsnError, Project, now

SCHEMA = "osnovanie-replay/1"
CHUNK_EVENTS = 1500
OUT_HEAD = 400
GATE_ANSWER = re.compile(r"^\s*(G[1-4]|R)\s+(да|правка|нет)\b", re.I)

SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9_\-]{10,}", r"\bsk-[A-Za-z0-9]{20,}", r"tvly-[A-Za-z0-9]{10,}", r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}", r"\bAKIA[0-9A-Z]{16}\b", r"xox[abprs]-[A-Za-z0-9\-]{10,}", r"AIza[0-9A-Za-z_\-]{30,}",
    r"Bearer\s+[A-Za-z0-9._\-]{16,}", r"(?i:api[_-]?key|token|secret|password|пароль|ключ)\s*[=:]\s*['\"]?[^\s'\"]{8,}",
]
SECRET_RE = re.compile("|".join(f"(?:{p})" for p in SECRET_PATTERNS))
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")
NOISE_RE = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z]+|user-prompt-submit-hook)>.*?</\1>", re.S)


class Redactor:
    def __init__(self, root: Path):
        self.root = str(root)
        self.home = str(Path.home())
        self.user = Path.home().name
        self.env_values = self._env_values(root)

    @staticmethod
    def _env_values(root: Path) -> list[str]:
        values = []
        for path in list(root.glob(".env*")) + list(root.glob("projects/*/.env*")):
            if path.name.endswith(".example") or not path.is_file():
                continue
            for line in path.read_text("utf-8", "replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    value = line.split("=", 1)[1].strip().strip("'\"")
                    if len(value) >= 8:
                        values.append(value)
        for key, value in os.environ.items():
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD", key) and len(value) >= 8:
                values.append(value)
        return sorted(set(values), key=len, reverse=True)

    def __call__(self, text: str | None) -> str:
        if not text:
            return ""
        for value in self.env_values:
            text = text.replace(value, "[секрет]")
        text = SECRET_RE.sub("[секрет]", text)
        text = text.replace(self.root, "<папка агента>").replace(self.home, "~")
        text = re.sub(rf"\b{re.escape(self.user)}\b", "<пользователь>", text) if len(self.user) > 2 else text
        text = EMAIL_RE.sub("[почта]", text)
        return PHONE_RE.sub("[телефон]", text)

    def leaks(self, blob: str) -> list[str]:
        found = [m.group(0)[:12] + "…" for m in SECRET_RE.finditer(blob)]
        found += [v[:4] + "…" for v in self.env_values if v in blob]
        if self.home in blob:
            found.append("домашняя папка")
        return found


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000


def _lines(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text("utf-8", "replace").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("type") in {"user", "assistant", "system", "cost-state"} and entry.get("timestamp"):
            out.append(entry)
    return out


def _osn_command(command: str) -> str | None:
    found = re.search(r"(?:bin/)?osnovanie\s+(?:--json\s+)?(?:--project\s+\S+\s+)?([a-z-]+)(?:\s+([a-z-]+))?", command)
    if not found:
        return None
    sub, action = found.groups()
    return f"{sub} {action}" if action and not action.startswith("-") and len(action) < 14 else sub


def _tool_line(name: str, data: dict) -> tuple[str, str | None]:
    base = lambda p: Path(str(p or "")).name or "файл"
    if name == "Bash":
        command = str(data.get("command", ""))
        osn = _osn_command(command)
        if osn:
            return f"osnovanie {osn}", osn
        return (data.get("description") or command.splitlines()[0] if command else "команда")[:120], None
    lines = {
        "Read": f"прочитал {base(data.get('file_path'))}",
        "Write": f"записал {base(data.get('file_path'))}",
        "Edit": f"поправил {base(data.get('file_path'))}",
        "Grep": f"искал «{str(data.get('pattern', ''))[:40]}» в файлах",
        "Glob": "искал файлы",
        "WebFetch": "открыл " + re.sub(r"^https?://([^/]+).*", r"\1", str(data.get("url", ""))),
        "WebSearch": f"искал в сети: {str(data.get('query', ''))[:60]}",
        "Agent": f"субагент {data.get('subagent_type') or data.get('description') or ''}".strip(),
        "Task": f"субагент {data.get('subagent_type') or data.get('description') or ''}".strip(),
    }
    return lines.get(name, name), None


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _transcript_events(path: Path, session: str, agent: str, clean: Redactor, table: dict) -> tuple[list[dict], dict]:
    events: list[dict] = []
    tools: dict[str, dict] = {}
    seen_messages: dict[str, dict] = {}
    info = {"model": None, "version": None, "cost_state_usd": None}
    for entry in _lines(path):
        t = _ts(entry["timestamp"])
        kind = entry["type"]
        if kind == "cost-state":
            info["cost_state_usd"] = entry.get("totalCostUSD")
            continue
        info["version"] = info["version"] or entry.get("version")
        message = entry.get("message") or {}
        if entry.get("isCompactSummary"):
            events.append({"t": t, "k": "ctx", "op": "compact", "s": session, "agent": agent})
            continue
        if kind == "system":
            if entry.get("preventedContinuation") or entry.get("level") == "error" and entry.get("hookInfos"):
                events.append({"t": t, "k": "guard", "s": session, "agent": agent, "reason": clean(str(entry.get("stopReason") or entry.get("content") or "хук остановил действие"))[:300]})
            continue
        if kind == "assistant":
            if message.get("model") == "<synthetic>":
                continue
            msg_id = str(message.get("id") or entry.get("uuid"))
            record = seen_messages.get(msg_id)
            if record is None:
                record = {"t": t, "k": "agent", "s": session, "agent": agent, "text": "", "model": message.get("model"), "usage": {}, "usd": 0.0}
                seen_messages[msg_id] = record
                events.append(record)
                info["model"] = info["model"] or message.get("model")
            if message.get("usage"):
                u = message["usage"]
                record["usage"] = {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0), "cr": u.get("cache_read_input_tokens", 0), "cw": u.get("cache_creation_input_tokens", 0)}
                record["usd"] = round(cost.usd(message.get("model", ""), u, table), 6)
            for block in message.get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    record["text"] = (record["text"] + "\n\n" + clean(block["text"])).strip()
                elif block.get("type") == "tool_use":
                    line, osn = _tool_line(block.get("name", ""), block.get("input") or {})
                    tool = {"t": t, "k": "tool", "s": session, "agent": agent, "name": block.get("name"), "line": clean(line), "ok": None, "dur": None, "out_head": ""}
                    if osn:
                        tool["cmd"] = osn
                    tools[str(block.get("id"))] = tool
                    events.append(tool)
            continue
        content = message.get("content")
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                tool = tools.get(str(part.get("tool_use_id")))
                if not tool:
                    continue
                text = _result_text(part.get("content"))
                tool["ok"] = not part.get("is_error")
                tool["dur"] = max(0, round(t - tool["t"]))
                tool["out_head"] = "[текст из карантина не показывается]" if "materials/quarantine" in text else clean(NOISE_RE.sub("", text))[:OUT_HEAD]
            continue
        if entry.get("isMeta"):
            continue
        text = content if isinstance(content, str) else _result_text(content)
        text = clean(NOISE_RE.sub("", text)).strip()
        if not text or text.startswith("<"):
            continue
        if text.startswith("[Request interrupted"):
            events.append({"t": t, "k": "ctx", "op": "interrupt", "s": session, "agent": agent})
            continue
        event = {"t": t, "k": "user", "s": session, "text": text[:4000]}
        answer = GATE_ANSWER.match(text)
        if answer:
            event["gate"] = answer.group(1).upper()
        events.append(event)
    events = [e for e in events if e["k"] != "agent" or e["text"] or e["usd"]]
    return events, info


def _state_events(project: Project) -> list[dict]:
    mapping = {
        "stage": lambda r: {"k": "stage", "stage": r.get("stage")},
        "source_added": lambda r: {"k": "source", "id": r.get("id"), "op": "quarantined" if r.get("quarantined") else "added"},
        "claim_added": lambda r: {"k": "claim", "id": r.get("id"), "op": "added", "type": r.get("type")},
        "claim_retracted": lambda r: {"k": "claim", "id": r.get("id"), "op": "retracted"},
        "claim_superseded": lambda r: {"k": "claim", "id": r.get("id"), "op": "superseded", "new_id": r.get("new_id")},
        "verify": lambda r: {"k": "verify", "verified": r.get("verified"), "contested": r.get("contested")},
        "option": lambda r: {"k": "option", "id": r.get("id"), "op": "added"},
        "model_run": lambda r: {"k": "model", "op": "run"},
        "review": lambda r: {"k": "review", "id": f"R{r.get('number')}", "leader_changed": r.get("leader_changed")},
        "human_time": lambda r: {"k": "human_time", "minutes": r.get("minutes"), "note": r.get("note")},
        "commitment": lambda r: {"k": "commitment", "id": r.get("id")},
        "paused": lambda r: {"k": "ctx", "op": "pause"},
        "resumed": lambda r: {"k": "ctx", "op": "resume"},
    }
    out = []
    for record in project.records("events"):
        kind = record.get("kind", "")
        if kind.startswith("gate_"):
            event = {"k": "gate", "gate": record.get("gate"), "op": kind[5:], "channel": record.get("channel")}
        elif kind in mapping:
            event = mapping[kind](record)
        else:
            continue
        out.append({"t": _ts(record["ts"]), "src": "state", **event})
    return out


def _claims(project: Project, t0: float, clean: Redactor) -> dict:
    out: dict[str, dict] = {}
    for record in project.records("claims"):
        cid = record["id"]
        item = out.setdefault(cid, {"versions": []})
        when = record.get("updated_at") or record.get("created_at")
        item["versions"].append({"rev": record.get("rev", 1), "t": round(_ts(when) - t0) if when else None, "status": record.get("status")})
        quote = record.get("quote")
        item.update({
            "type": record["type"], "topic": record.get("topic"), "text": clean(record["text"]), "status": record["status"],
            "source_id": record.get("source_id"), "quote": clean(quote) if quote else None, "locator": record.get("locator"),
            "value": record.get("value"), "unit": record.get("unit"), "period": record.get("period"),
            "depends_on": record.get("depends_on", []), "confidence": record.get("confidence"), "expires": record.get("expires"),
            "supersedes": record.get("supersedes"), "superseded_by": record.get("superseded_by"),
        })
    current = project.current("claims")
    for cid, item in out.items():
        if cid in current:
            item["label"] = claims_mod.LABEL[claims_mod.effective_type(current[cid])]
    return out


def _layout(claims: dict) -> dict:
    depth: dict[str, int] = {}

    def level(cid: str, trail: frozenset = frozenset()) -> int:
        if cid in depth:
            return depth[cid]
        deps = [d for d in claims.get(cid, {}).get("depends_on", []) if d in claims and d not in trail]
        depth[cid] = 1 + max((level(d, trail | {cid}) for d in deps), default=-1)
        return depth[cid]

    for cid in claims:
        level(cid)
    layers: dict[int, list[str]] = {}
    for cid in sorted(claims, key=lambda c: (str(claims[c].get("topic") or ""), c)):
        layers.setdefault(depth[cid], []).append(cid)
    deepest = max(layers, default=0) or 1
    layout = {}
    for layer, ids in layers.items():
        for i, cid in enumerate(ids):
            layout[cid] = [round(layer / deepest, 4), round((i + 0.5) / len(ids), 4)]
    return layout


def _play_axis(events: list[dict]) -> None:
    play = 0.0
    previous = None
    for event in events:
        if previous is not None:
            gap = event["t"] - previous
            cap = 1500 if event["k"] == "user" else 4000
            play += max(120, min(gap, cap))
        event["p"] = round(play)
        previous = event["t"]


WEIGHTS = {"user": 3, "stage": 4, "review": 9, "guard": 5, "option": 2, "commitment": 3, "verify": 2}


def _weight(event: dict, first_user: bool) -> int:
    if event["k"] == "user" and first_user:
        return 10
    if event["k"] == "gate":
        return 7 if event.get("op") in {"approve", "revise", "reject"} else 4
    return WEIGHTS.get(event["k"], 0)


def _label(event: dict, first: bool = False) -> str:
    k = event["k"]
    if k == "user":
        return f"Человек: {event['gate']}" if event.get("gate") else ("Вопрос руководителя" if first else "Реплика человека")
    if k == "gate":
        return f"{event['gate']}: " + {"open": "агент остановился и ждёт", "approve": "человек принял", "revise": "человек поправил", "reject": "человек отклонил"}.get(event.get("op"), event.get("op", ""))
    if k == "stage":
        return f"Этап: {event.get('stage')}"
    if k == "review":
        return f"Пересмотр {event.get('id')}" + (": лидер сменился" if event.get("leader_changed") else "")
    if k == "guard":
        return "Защита остановила действие"
    if k == "verify":
        return f"Проверено цитат: {event.get('verified')}"
    return k


def _chapters_and_sixty(events: list[dict]) -> tuple[list[dict], list[dict]]:
    first = next((i for i, e in enumerate(events) if e["k"] == "user"), None)
    chapters = [{"t": e["t"], "p": e["p"], "i": e["i"], "label": _label(e, idx == first)} for idx, e in enumerate(events)
                if idx == first or e["k"] in {"stage", "review"} or (e["k"] == "gate" and e.get("op") != "open")]
    ranked = sorted(((_weight(e, idx == first), -idx) for idx, e in enumerate(events)), reverse=True)
    picked = sorted(-neg for weight, neg in ranked[:12] if weight > 0)
    span = 60000 // max(1, len(picked))
    sixty = [{"from": n * span, "to": (n + 1) * span, "i": events[idx]["i"], "label": _label(events[idx], idx == first)} for n, idx in enumerate(picked)]
    return chapters, sixty


def find_transcripts(root: Path, slug: str | None) -> list[Path]:
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    folder = config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root))
    found = []
    for path in sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        text = path.read_text("utf-8", "replace")
        if '"type":"assistant"' not in text.replace(" ", ""):
            continue
        if slug and f"projects/{slug}" not in text and f"--project {slug}" not in text and f"new {slug}" not in text:
            continue
        found.append(path)
    return found


def build(root: Path, transcripts: list[Path], project: Project | None, run_id: str, title: str, mode: str = "agent") -> dict:
    if not transcripts:
        raise OsnError("Нет журналов Claude Code для реплея: укажите --session или запустите прогон.")
    clean = Redactor(root)
    table = cost.price_table()
    events: list[dict] = []
    sessions = []
    info_all = []
    for n, path in enumerate(transcripts, 1):
        session = f"s{n}"
        found, info = _transcript_events(path, session, "main", clean, table)
        sub_dir = path.with_suffix("") / "subagents"
        for sub in sorted(sub_dir.glob("*.jsonl")) if sub_dir.exists() else []:
            meta_path = sub.with_suffix(".meta.json")
            agent = json.loads(meta_path.read_text("utf-8")).get("agentType", "subagent") if meta_path.exists() else "subagent"
            sub_events, _ = _transcript_events(sub, session, agent, clean, table)
            found += sub_events
        if found:
            sessions.append({"id": session, "start": min(e["t"] for e in found), "end": max(e["t"] for e in found)})
        events += found
        info_all.append(info)
    if project and project.exists():
        events += _state_events(project)
    if not events:
        raise OsnError("В журналах нет событий.")
    t0 = min(e["t"] for e in events)
    events.sort(key=lambda e: (e["t"], 0 if e.get("src") == "state" else 1))
    cum = 0.0
    rate = table["usd_rub"]
    for i, event in enumerate(events):
        event["i"] = i
        event["t"] = round(event["t"] - t0)
        event.pop("src", None)
        if event["k"] == "agent":
            cum += event["usd"]
            event["cum_usd"] = round(cum, 4)
    _play_axis(events)
    for s in sessions:
        s["start"], s["end"] = round(s["start"] - t0), round(s["end"] - t0)
    for prev, nxt in zip(sessions, sessions[1:]):
        nxt["resume_of"] = prev["id"]
    claims = _claims(project, t0, clean) if project and project.exists() else {}
    sources = {}
    if project and project.exists():
        for sid, s in project.current("sources").items():
            sources[sid] = {"title": clean(s.get("title")), "url": s.get("url"), "kind": s.get("kind"), "retrieved_at": (s.get("accessed_at") or "")[:10],
                            "published_at": s.get("published_at"), "sha256": s.get("sha256"), "quarantined": s.get("status") == "quarantined"}
    chapters, sixty = _chapters_and_sixty(events)
    agent_events = [e for e in events if e["k"] == "agent"]
    tokens = {key: sum(e["usage"].get(key, 0) for e in agent_events) for key in ("in", "out", "cr", "cw")}
    human_min = sum(e.get("minutes") or 0 for e in events if e["k"] == "human_time")
    first_user = next((e["text"] for e in events if e["k"] == "user"), "")
    cost_state = sum(i["cost_state_usd"] or 0 for i in info_all) or None
    checks = []
    if cost_state and abs(cost_state - cum) / cost_state > 0.02:
        checks.append(f"цена по прайсу {cum:.2f} $ расходится с итогом Claude Code {cost_state:.2f} $")
    digest = hashlib.sha256()
    for path in transcripts:
        digest.update(path.read_bytes())
    return {
        "schema": SCHEMA, "id": run_id, "title": title, "built_at": now(), "source_hash": "sha256:" + digest.hexdigest(),
        "run": {"model": next((i["model"] for i in info_all if i["model"]), None), "harness": "claude-code", "version": next((i["version"] for i in info_all if i["version"]), None),
                "project": project.slug if project else None, "mode": mode, "question": first_user[:1000], "sessions": sessions},
        "prices": {"usd_rub": rate, "human_rub_per_hour": table["human_rub_per_hour"], "checked_at": table["checked_at"]},
        "t_axes": {"real_ms": [0, events[-1]["t"]], "play_ms": [0, events[-1]["p"]]},
        "chapters": chapters, "sixty": sixty,
        "claims": claims, "sources": sources, "graph": {"layout": _layout(claims)},
        "totals": {"usd": round(cum, 4), "rub": round(cum * rate, 2), "tokens": tokens, "human_min": human_min,
                   "calls": len(agent_events), "tools": sum(1 for e in events if e["k"] == "tool"),
                   "claims": dict(Counter(c["type"] for c in claims.values())), "events": len(events)},
        "checks": checks,
        "events": events,
    }


def write(replay: dict, out: Path, root: Path) -> dict:
    clean = Redactor(root)
    events = replay.pop("events")
    chunks = [events[i:i + CHUNK_EVENTS] for i in range(0, len(events), CHUNK_EVENTS)] or [[]]
    index = {**replay, "events": chunks[0], "chunks": []}
    files = {}
    for n, chunk in enumerate(chunks[1:], 2):
        name = f"chunks/events-{n}.json"
        index["chunks"].append({"name": f"events-{n}", "from": chunk[0]["t"], "to": chunk[-1]["t"], "url": name})
        files[name] = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
    files["replay.json"] = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    leaks = sorted({leak for blob in files.values() for leak in clean.leaks(blob)})
    if leaks:
        raise OsnError(f"Реплей не записан: после вычистки остались похожие на секрет фрагменты ({', '.join(leaks[:5])}).")
    out.mkdir(parents=True, exist_ok=True)
    for name, blob in files.items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blob, "utf-8")
    return {"out": str(out), "events": len(events), "claims": len(replay["claims"]), "usd": replay["totals"]["usd"], "rub": replay["totals"]["rub"],
            "kb": round(sum(len(b.encode()) for b in files.values()) / 1024, 1), "checks": replay["checks"]}
