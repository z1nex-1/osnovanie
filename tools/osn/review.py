from __future__ import annotations

import difflib
import re
import shutil

from . import checks, claims as claims_mod, model, workflow
from .store import OsnError, PolicyError, Project, atomic_write, now, write_json

DOCS = ["situation.md", "product.md", "communication.md", "validation.md"]


def snapshot(project: Project, label: str) -> str:
    root = project.outputs / "versions"
    root.mkdir(parents=True, exist_ok=True)
    numbers = [int(p.name[1:]) for p in root.glob("v*") if p.name[1:].isdigit()]
    name = f"v{max(numbers, default=0) + 1}"
    target = root / name
    target.mkdir()
    for doc in DOCS:
        path = project.outputs / doc
        if path.exists():
            shutil.copy2(path, target / doc)
    write_json(target / "meta.json", {"version": name, "ts": now(), "label": label, "decision": project.meta().get("decision")})
    project.event("snapshot", version=name, label=label)
    return name


def _downstream(claims: dict[str, dict], changed: set[str]) -> set[str]:
    reverse: dict[str, set[str]] = {}
    for claim in claims.values():
        for dep in claim.get("depends_on", []):
            reverse.setdefault(dep, set()).add(claim["id"])
    seen: set[str] = set()
    frontier = list(changed)
    while frontier:
        for child in reverse.get(frontier.pop(), ()):
            if child not in seen and child not in changed:
                seen.add(child)
                frontier.append(child)
    return seen


def _pending(project: Project, claims: dict[str, dict]) -> set[str]:
    handled = {c for r in project.records("reviews") for c in r["changed"]}
    return {c["id"] for c in claims.values() if (c.get("superseded_by") or c["status"] == "retracted") and c["id"] not in handled}


def _paragraphs(project: Project, touched: set[str]) -> list[dict]:
    found = []
    for doc in DOCS:
        path = project.outputs / doc
        if not path.exists():
            continue
        for paragraph in checks.paragraphs(path.read_text("utf-8")):
            refs = set(claims_mod.refs_in(paragraph)) & touched
            if refs:
                found.append({"doc": doc, "refs": sorted(refs), "text": paragraph[:200]})
    return found


def preview(project: Project, changed: set[str] | None = None) -> dict:
    claims = project.current("claims")
    changed = changed or _pending(project, claims)
    unknown = changed - set(claims)
    if unknown:
        raise OsnError(f"Нет утверждений: {', '.join(sorted(unknown))}.")
    down = _downstream(claims, changed)
    touched = changed | down
    options = [o["id"] for o in project.current("options").values() if set(o.get("basis", [])) & touched]
    commitments = [c["id"] for c in project.current("commitments").values() if set(c.get("claims", [])) & touched]
    params = []
    last = model.last_run(project)
    if last:
        params = [name for name, p in last["params"].items() if p.get("claim") in touched]
    return {"changed": sorted(changed), "downstream": sorted(down), "options": options, "commitments": commitments, "params": params, "paragraphs": _paragraphs(project, touched)}


def run(project: Project, changed: set[str] | None = None, note: str = "") -> dict:
    claims = project.current("claims")
    impact = preview(project, changed)
    if not impact["changed"]:
        raise PolicyError("Нет заменённых или отозванных утверждений. Сначала osnovanie claim supersede или retract.")
    with project.lock():
        for claim_id in impact["downstream"]:
            if claims[claim_id]["status"] != "retracted":
                claims_mod.update(project, claim_id, status="stale", stale_reason=f"изменилось основание: {', '.join(impact['changed'])}")
    before = model.last_run(project)
    after = None
    model_error = None
    if before:
        try:
            after = model.run(project, tag="review")
        except OsnError as error:
            model_error = str(error)
    leader_changed = bool(before and after and before["ranking"][0] != after["ranking"][0])
    number = len(project.records("reviews")) + 1
    lines = [f"# Пересмотр {number}", ""]
    if note:
        lines += [f"Причина: {note}", ""]
    lines += ["## Что изменилось", ""]
    for claim_id in impact["changed"]:
        old = claims[claim_id]
        if old.get("superseded_by"):
            new = claims[old["superseded_by"]]
            lines.append(f"- {claim_id} → {new['id']}: было «{old['text']}», стало «{new['text']}» (источник {new.get('source_id')})")
        else:
            lines.append(f"- {claim_id} отозвано: {old.get('retract_reason')}")
    lines += ["", "## Что затронуто", "", f"- Зависимые утверждения: {', '.join(impact['downstream']) or 'нет'}", f"- Варианты: {', '.join(impact['options']) or 'нет'}", f"- Величины модели: {', '.join(impact['params']) or 'нет'}", f"- Обязательства: {', '.join(impact['commitments']) or 'нет'}", ""]
    if before and after:
        unit = after["output_unit"]
        lines += ["## Как изменился расчёт", "", f"| Вариант | База было, {unit} | База стало, {unit} | Лидерство было | Лидерство стало |", "|---|---:|---:|---:|---:|"]
        for option in after["results"]:
            a, b = before["results"][option], after["results"][option]
            lines.append(f"| {option} | {a['base']:,.0f} | {b['base']:,.0f} | {a['p_best'] * 100:.0f}% | {b['p_best'] * 100:.0f}% |")
        verdict = f"Лидер сменился: {before['ranking'][0]} → {after['ranking'][0]}. Решение нужно принять заново." if leader_changed else f"Лидер прежний: {after['ranking'][0]}."
        lines += ["", verdict, ""]
    if model_error:
        lines += [f"Модель не пересчитана: {model_error}", ""]
    lines += ["## Абзацы, которые надо переписать", ""]
    lines += [f"- `{p['doc']}` ({', '.join(p['refs'])}): {p['text']}" for p in impact["paragraphs"]] or ["- нет"]
    lines += ["", "Агент не меняет решение сам. Ответьте: `R да` или `R правка: ...`."]
    report = f"review-{number}.md"
    atomic_write(project.outputs / report, "\n".join(lines) + "\n")
    project.append("reviews", {"id": f"R{number}", "ts": now(), "changed": impact["changed"], "downstream": impact["downstream"], "options": impact["options"], "paragraphs": len(impact["paragraphs"]), "leader_before": before["ranking"][0] if before else None, "leader_after": after["ranking"][0] if after else None, "leader_changed": leader_changed, "note": note, "report": report})
    project.event("review", number=number, leader_changed=leader_changed)
    workflow.open_gate(project, "R", f"Подробности: outputs/{report}")
    return {**impact, "report": f"outputs/{report}", "leader_changed": leader_changed, "ranking": after["ranking"] if after else None}


def after_approval(project: Project) -> None:
    reviews = project.records("reviews")
    if not reviews or not reviews[-1]["leader_changed"]:
        return
    state = workflow.load(project)
    state["stage"] = "strategy"
    state["gates"].pop("G4", None)
    workflow.save(project, state)
    project.event("decision_reopened", review=reviews[-1]["id"])


def finish(project: Project) -> dict:
    problems = checks.doc_problems(project)
    if problems:
        raise PolicyError("Документы ещё опираются на старое:\n- " + "\n- ".join(problems[:12]))
    return {"version": snapshot(project, "после пересмотра")}


def diff(project: Project, left: str | None, right: str | None) -> str:
    root = project.outputs / "versions"
    versions = sorted((p.name for p in root.glob("v*") if p.name[1:].isdigit()), key=lambda n: int(n[1:])) if root.exists() else []
    if len(versions) < 1:
        raise OsnError("Сохранённых версий нет.")
    right = right or versions[-1]
    left = left or (versions[-2] if len(versions) > 1 else right)
    if not re.fullmatch(r"v\d+", left) or not re.fullmatch(r"v\d+", right):
        raise OsnError("Версия задаётся как v1, v2.")
    chunks = []
    for doc in DOCS:
        a, b = root / left / doc, root / right / doc
        chunks += difflib.unified_diff(
            a.read_text("utf-8").splitlines() if a.exists() else [],
            b.read_text("utf-8").splitlines() if b.exists() else [],
            f"{left}/{doc}", f"{right}/{doc}", lineterm="",
        )
    return "\n".join(chunks) or "Различий нет."


def add_commitment(project: Project, data: dict) -> dict:
    state = workflow.load(project)
    if state["gates"].get("G4", {}).get("status") != "approved":
        raise PolicyError("Обязательства записываются после решения человека в точке G4.")
    for field in ("what", "owner", "due", "metric", "threshold", "review_if"):
        if not data.get(field):
            raise OsnError(f"У обязательства нет поля {field}.")
    claims = project.current("claims")
    bad = [c for c in data.get("claims", []) if c not in claims]
    if bad:
        raise OsnError(f"Нет утверждений: {', '.join(bad)}.")
    record = {"id": project.next_id("commitments", "k"), "ts": now(), "status": "active", "observations": [], **data}
    project.append("commitments", record)
    project.event("commitment", id=record["id"])
    return record


def add_metric(project: Project, commitment_id: str, value: float, note: str) -> dict:
    current = project.current("commitments")
    if commitment_id not in current:
        raise OsnError(f"Обязательство {commitment_id} не найдено.")
    record = dict(current[commitment_id])
    record["observations"] = record["observations"] + [{"ts": now(), "value": value, "note": note}]
    record["ts"] = now()
    project.append("commitments", record)
    return record


def what_next(project: Project) -> list[dict]:
    from datetime import date, datetime

    items: list[dict] = []
    state = workflow.load(project)
    if state["awaiting"]:
        items.append({"priority": 0, "kind": "gate", "text": f"Ждём вашего решения в точке {state['awaiting']}: outputs/gates/{state['awaiting']}.md"})
    claims = project.current("claims")
    decision = project.records("decisions")
    bases = set(decision[-1].get("bases", [])) if decision else set()
    outdated = [c for c in bases if c in claims and claims_mod.effective_type(claims[c]) in {"stale", "retracted"}]
    if outdated:
        items.append({"priority": 1, "kind": "bases", "text": f"Основания решения устарели: {', '.join(sorted(outdated))}. Запустите osnovanie review run."})
    pending = _pending(project, claims)
    if pending:
        items.append({"priority": 1, "kind": "bases", "text": f"Заменённые утверждения ещё не прошли пересмотр: {', '.join(sorted(pending))}."})
    today = date.today()
    for item in project.current("commitments").values():
        if item["status"] != "active":
            continue
        days = (date.fromisoformat(item["due"]) - today).days
        if days < 0:
            items.append({"priority": 1, "kind": "overdue", "text": f"{item['id']} просрочено на {-days} дн.: {item['what']} ({item['owner']})"})
        elif days <= 14:
            items.append({"priority": 2, "kind": "due", "text": f"{item['id']} через {days} дн.: {item['what']} ({item['owner']})"})
        if item["observations"]:
            items.append({"priority": 2, "kind": "metric", "text": f"{item['id']}: {item['metric']} = {item['observations'][-1]['value']} при пороге {item['threshold']}. Условие пересмотра: {item['review_if']}"})
    for source in project.current("sources").values():
        age = (today - datetime.fromisoformat(source["accessed_at"]).date()).days
        if age > 90 and source["status"] == "active":
            items.append({"priority": 3, "kind": "fresh", "text": f"Источник {source['id']} получен {age} дн. назад: {source['title']}"})
    return sorted(items, key=lambda i: i["priority"])
