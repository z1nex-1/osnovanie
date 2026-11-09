from __future__ import annotations

import re
import sys

from . import checks
from .store import OsnError, PolicyError, Project, atomic_write, now, read_json, write_json

STAGES = ["intake", "plan", "research", "options", "strategy", "decided"]
STAGE_LABEL = {
    "intake": "постановка", "plan": "план и источники", "research": "исследование",
    "options": "варианты", "strategy": "стратегии", "decided": "решение принято, сопровождение",
}
GATE_AT = {"intake": "G1", "plan": "G2", "options": "G3", "strategy": "G4"}
GATE_LABEL = {
    "G1": "постановка задачи", "G2": "источники и бюджет", "G3": "варианты", "G4": "решение", "R": "пересмотр при новых данных",
}
NEXT_STAGE = {"intake": "plan", "plan": "research", "research": "options", "options": "strategy", "strategy": "decided"}
NEXT_HINT = {
    "intake": "Заполните постановку (osnovanie brief set) и откройте точку G1.",
    "plan": "Составьте вопросы, меняющие решение, план источников и бюджет, откройте точку G2.",
    "research": "Загрузите источники, запишите утверждения, проверьте цитаты, закройте вопросы, затем osnovanie advance.",
    "options": "Запишите варианты с возражениями, посчитайте модель, откройте точку G3.",
    "strategy": "Напишите product.md, communication.md, validation.md, пройдите osnovanie check, откройте точку G4.",
    "decided": "Запишите обязательства. При новых данных: osnovanie review run.",
}


def state_path(project: Project):
    return project.state / "workflow.json"


def load(project: Project) -> dict:
    return read_json(state_path(project), {"stage": "intake", "awaiting": None, "paused": False, "gates": {}})


def save(project: Project, state: dict) -> None:
    write_json(state_path(project), state)


def advance(project: Project) -> dict:
    with project.lock():
        state = load(project)
        _ensure_running(state)
        stage = state["stage"]
        if state["awaiting"]:
            raise PolicyError(f"Ждём решения человека в точке {state['awaiting']}. Агент дальше не идёт.")
        gate = GATE_AT.get(stage)
        if gate and state["gates"].get(gate, {}).get("status") != "approved":
            raise PolicyError(f"Этап «{STAGE_LABEL[stage]}» закрывается только через точку {gate}: osnovanie gate open {gate}.")
        if stage == "decided":
            raise OsnError("Работа уже на этапе сопровождения.")
        problems = checks.failures(project, stage)
        if problems:
            raise PolicyError("Этап нельзя закрыть:\n- " + "\n- ".join(problems))
        state["stage"] = NEXT_STAGE[stage]
        save(project, state)
        project.event("stage", stage=state["stage"])
        return state


def _ensure_running(state: dict) -> None:
    if state.get("paused"):
        raise PolicyError("Работа остановлена человеком. Продолжить может только человек: сообщение «продолжить».")


def open_gate(project: Project, gate: str, summary: str | None) -> dict:
    with project.lock():
        state = load(project)
        _ensure_running(state)
        if gate not in GATE_LABEL:
            raise OsnError("Точки человека: G1, G2, G3, G4, R.")
        if gate != "R" and GATE_AT.get(state["stage"]) != gate:
            raise PolicyError(f"Сейчас этап «{STAGE_LABEL[state['stage']]}», точку {gate} открыть нельзя.")
        if state["awaiting"] and state["awaiting"] != gate:
            raise PolicyError(f"Уже открыта точка {state['awaiting']}.")
        problems = checks.failures(project, state["stage"]) if gate != "R" else []
        if problems:
            raise PolicyError(f"Точку {gate} открыть нельзя:\n- " + "\n- ".join(problems))
        state["awaiting"] = gate
        state["gates"][gate] = {"status": "open", "opened_at": now(), "round": state["gates"].get(gate, {}).get("round", 0) + 1}
        save(project, state)
        packet = checks.gate_packet(project, gate, summary)
        atomic_write(project.outputs / "gates" / f"{gate}.md", packet)
        project.append("gates", {"ts": now(), "gate": gate, "action": "open", "round": state["gates"][gate]["round"]})
        project.event("gate_open", gate=gate)
        return {"gate": gate, "packet": f"outputs/gates/{gate}.md", "text": packet}


def _record(project: Project, gate: str, action: str, channel: str, comment: str, extra: dict) -> dict:
    state = load(project)
    if state["awaiting"] != gate:
        raise PolicyError(f"Точка {gate} сейчас не открыта, ответ не принят.")
    info = state["gates"][gate]
    info.update(status="approved" if action == "approve" else "revise", closed_at=now(), comment=comment, channel=channel, **extra)
    state["awaiting"] = None
    save(project, state)
    project.append("gates", {"ts": now(), "gate": gate, "action": action, "channel": channel, "comment": comment, **extra})
    project.event("gate_" + action, gate=gate, channel=channel)
    return info


def apply_human(project: Project, gate: str, action: str, channel: str, comment: str = "", options: list[str] | None = None, budget: float | None = None, owner: str | None = None) -> dict:
    """Единственная точка записи решения человека. Вызывается только из TTY и из хука промпта."""
    with project.lock():
        extra: dict = {}
        if options:
            known = project.current("options")
            bad = [o for o in options if o not in known]
            if bad:
                raise OsnError(f"Нет таких вариантов: {', '.join(bad)}.")
            extra["options"] = options
        if owner:
            extra["owner"] = owner
        if action == "approve" and gate == "G4" and len(options or []) != 1:
            raise OsnError("В точке G4 нужно выбрать один вариант: G4 да O2.")
        info = _record(project, gate, action, channel, comment, extra)
        meta = project.meta()
        if budget is not None:
            meta["budget_usd"] = budget
        if action == "approve" and gate == "G2":
            from urllib.parse import urlparse

            domains = {urlparse(r["url"]).hostname for r in project.records("source_plan") if r["url"].startswith("http")}
            meta["approved_domains"] = sorted(d for d in domains if d)
        if action == "approve" and gate == "G3":
            meta["shortlist"] = options or sorted(project.current("options"))
        if action == "approve" and gate == "G4":
            meta["decision"] = {"option": options[0], "owner": owner, "ts": now(), "comment": comment}
            project.append("decisions", {"id": project.next_id("decisions", "d"), "ts": now(), "option": options[0], "owner": owner, "comment": comment, "by": channel, "bases": _bases(project)})
        if action == "approve" and gate == "R":
            meta["review_accepted"] = now()
        project.save_meta(meta)
        if action == "approve" and gate in {"G4", "R"}:
            from . import review

            if gate == "G4":
                review.snapshot(project, "решение G4")
            else:
                review.after_approval(project)
        return info


def _bases(project: Project) -> list[str]:
    from .claims import refs_in

    bases: set[str] = set()
    for name in ("product.md", "communication.md", "validation.md"):
        path = project.outputs / name
        if path.exists():
            bases.update(refs_in(path.read_text("utf-8")))
    return sorted(bases)


def require_tty(what: str) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise PolicyError(f"{what} подтверждает только человек в своём терминале или сообщением в чате. Агент этого сделать не может.")


GATE_PROMPT = re.compile(
    r"^\s*(G[1-4]|R)\s+(да|ок|ok|утверждаю|принято|правка|исправить|нет)\b[ \t:,.-]*(.*)$", re.I | re.S
)
CONFIRM_PROMPT = re.compile(r"^\s*подтверждаю\s+((?:c\d{3,}[\s,]*)+)(.*)$", re.I | re.S)


def parse_human_prompt(text: str) -> dict | None:
    if re.fullmatch(r"\s*(стоп|пауза|stop)\s*[.!]?\s*", text, re.I):
        return {"kind": "pause"}
    if re.fullmatch(r"\s*продолжить\s*[.!]?\s*", text, re.I):
        return {"kind": "resume"}
    confirm = CONFIRM_PROMPT.match(text)
    if confirm:
        return {"kind": "confirm", "claims": re.findall(r"c\d{3,}", confirm.group(1)), "comment": confirm.group(2).strip()}
    match = GATE_PROMPT.match(text)
    if not match:
        return None
    gate, verb, rest = match.group(1).upper(), match.group(2).lower(), match.group(3).strip()
    action = "approve" if verb in {"да", "ок", "ok", "утверждаю", "принято"} else "revise"
    options = re.findall(r"\bO\d+\b", rest)
    budget_match = re.search(r"бюджет\s+(\d+(?:[.,]\d+)?)", rest, re.I)
    owner_match = re.search(r"ответственн\w*\s+([^,.;\n]+)", rest, re.I)
    return {
        "kind": "gate", "gate": gate, "action": action, "options": options,
        "budget": float(budget_match.group(1).replace(",", ".")) if budget_match else None,
        "owner": owner_match.group(1).strip() if owner_match else None, "comment": rest,
    }


def handle_human_prompt(project: Project, text: str, channel: str) -> str | None:
    parsed = parse_human_prompt(text)
    if not parsed:
        return None
    if parsed["kind"] == "pause":
        with project.lock():
            state = load(project)
            state["paused"] = True
            save(project, state)
            project.event("paused")
        return "Человек остановил работу. Не выполняй шагов, кратко сообщи, на чём остановились."
    if parsed["kind"] == "resume":
        with project.lock():
            state = load(project)
            state["paused"] = False
            save(project, state)
            project.event("resumed")
        return "Человек разрешил продолжать. Начни с osnovanie resume."
    if parsed["kind"] == "confirm":
        from . import claims as claims_mod

        done = []
        with project.lock():
            for claim_id in parsed["claims"]:
                claims_mod.update(project, claim_id, status="verified", verified_by=f"human:{channel}", verified_at=now(), verify_note=parsed["comment"])
                done.append(claim_id)
            project.event("human_confirm", claims=done, channel=channel)
        return f"Человек подтвердил утверждения: {', '.join(done)}."
    try:
        apply_human(project, parsed["gate"], parsed["action"], channel, parsed["comment"], parsed["options"], parsed["budget"], parsed["owner"])
    except OsnError as error:
        return f"Ответ на точку {parsed['gate']} не принят: {error}"
    if parsed["action"] == "approve":
        return f"Человек утвердил точку {parsed['gate']}. Запись сделана хуком. Продолжай: osnovanie advance, затем следующий этап."
    return f"Человек вернул точку {parsed['gate']} на правку: {parsed['comment']}. Исправь и открой точку снова."
