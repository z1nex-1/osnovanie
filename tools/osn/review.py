from __future__ import annotations

import difflib
import re
import shutil

from . import checks, claims as claims_mod, model, workflow
from .store import OsnError, PolicyError, Project, atomic_write, now, write_json

TYPE_RU = {"fact": "факт", "calculation": "расчёт", "hypothesis": "гипотеза", "forecast": "прогноз", "opinion": "мнение", "assumption": "допущение", "synthetic": "синтетика"}
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
    return set(_chains(claims, changed))


def _chains(claims: dict[str, dict], changed: set[str]) -> dict[str, list[str]]:
    """Для каждого задетого вывода — кратчайшая цепочка до изменившегося основания: [вывод, ..., основание]."""
    reverse: dict[str, set[str]] = {}
    for claim in claims.values():
        for dep in claim.get("depends_on", []):
            reverse.setdefault(dep, set()).add(claim["id"])
    parent: dict[str, str] = {}
    frontier = sorted(changed)
    while frontier:
        nxt = []
        for node in frontier:
            for child in sorted(reverse.get(node, ())):
                if child not in parent and child not in changed:
                    parent[child] = node
                    nxt.append(child)
        frontier = nxt
    chains = {}
    for child in parent:
        path, node = [child], child
        while node in parent:
            node = parent[node]
            path.append(node)
        chains[child] = path
    return chains


def _short(text: str, limit: int = 110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fallen(project: Project, claims: dict[str, dict], changed: list[str], before: dict | None, after: dict | None) -> dict:
    """Что упало и почему: цепочки выводов, величины модели было/стало, варианты, обязательства, что устояло."""
    cause = {}
    for claim_id in changed:
        old = claims[claim_id]
        if old.get("superseded_by"):
            new = claims[old["superseded_by"]]
            cause[claim_id] = {"was": old["text"], "now": new["text"], "new_id": new["id"], "source": new.get("source_id"), "quote": new.get("quote")}
        else:
            cause[claim_id] = {"was": old["text"], "now": None, "reason": old.get("retract_reason")}
    chains = _chains(claims, set(changed))
    rows = []
    for claim_id, path in sorted(chains.items(), key=lambda kv: (len(kv[1]), kv[0])):
        claim = claims[claim_id]
        rows.append({"id": claim_id, "type": claim.get("type"), "text": claim["text"], "path": path, "root": path[-1], "depth": len(path) - 1})
    params = []
    if before and after:
        for name, p in after["params"].items():
            old = before["params"].get(name)
            if old and (old["base"] != p["base"] or old.get("claim") != p.get("claim")):
                params.append({"name": name, "label": p["label"], "unit": p["unit"], "was": old["base"], "now": p["base"], "claim_was": old.get("claim"), "claim_now": p.get("claim")})
    options = []
    if before and after:
        for option, b in after["results"].items():
            a = before["results"].get(option, b)
            options.append({"id": option, "base_was": a["base"], "base_now": b["base"], "p_best_was": a["p_best"], "p_best_now": b["p_best"]})
    touched = set(changed) | set(chains)
    commitments = []
    for c in project.current("commitments").values():
        hit = set(c.get("claims", [])) & touched
        if hit:
            commitments.append({"id": c["id"], "what": c.get("what"), "review_if": c.get("review_if"), "claims": sorted(hit)})
    active = [c for c in claims.values() if c.get("status") not in ("retracted",) and not c.get("superseded_by")]
    held = [c["id"] for c in active if c["id"] not in touched]
    return {"cause": cause, "chains": rows, "params": params, "options": options, "commitments": commitments, "held": len(held), "total": len(active)}


def render_fallen(f: dict, claims: dict[str, dict]) -> list[str]:
    lines = ["## Что упало и почему", ""]
    for claim_id, c in f["cause"].items():
        if c["now"] is not None:
            quote = f" Цитата: «{_short(c['quote'], 160)}»" if c.get("quote") else ""
            lines.append(f"- Основание {claim_id} больше не верно: «{_short(c['was'])}». Теперь {c['new_id']}: «{_short(c['now'])}» (источник {c.get('source') or 'нет, это допущение'}).{quote}")
        else:
            lines.append(f"- Основание {claim_id} отозвано: «{_short(c['was'])}». Причина: {c.get('reason') or 'не указана'}.")
    if f["chains"]:
        lines += ["", "| Вывод | Тип | Почему под вопросом |", "|---|---|---|"]
        for row in f["chains"]:
            via = " → ".join(row["path"][1:])
            lines.append(f"| {row['id']} «{_short(row['text'], 80)}» | {TYPE_RU.get(row['type'], row['type'])} | держался на {via} |")
    if f["params"]:
        lines += ["", "Величины модели:", ""]
        lines += [f"- {p['label']}: было {p['was']:g}, стало {p['now']:g} {p['unit']} (основание {p['claim_was']} → {p['claim_now']})" for p in f["params"]]
    moved = [o for o in f["options"] if abs(o["p_best_now"] - o["p_best_was"]) >= 0.05 or o["base_was"] != o["base_now"]]
    if moved:
        lines += ["", "Варианты:", ""]
        for o in moved:
            money = lambda v: f"{v:,.0f}".replace(",", " ")  # noqa: E731
            lines.append(f"- {o['id']}: база {money(o['base_was'])} → {money(o['base_now'])}, лидер в {o['p_best_was'] * 100:.0f}% → {o['p_best_now'] * 100:.0f}% прогонов модели")
    if f["commitments"]:
        lines += ["", "Обязательства, которые держались на изменившемся:", ""]
        lines += [f"- {c['id']}: {c['what']}; условие пересмотра «{c['review_if']}» (опоры {', '.join(c['claims'])})" for c in f["commitments"]]
    lines += ["", f"Устояло {f['held']} из {f['total']} действующих утверждений: их основания не менялись.", ""]
    return lines


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
                found.append({"doc": doc, "refs": sorted(refs), "text": _short(paragraph, 220)})
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
    why = fallen(project, project.current("claims"), impact["changed"], before, after)
    lines += render_fallen(why, claims)
    lines += ["## Абзацы, которые надо переписать", ""]
    lines += [f"- `{p['doc']}` ({', '.join(p['refs'])}): {p['text']}" for p in impact["paragraphs"]] or ["- нет"]
    lines += ["", "Агент не меняет решение сам. Ответьте: `R да` или `R правка: ...`."]
    report = f"review-{number}.md"
    atomic_write(project.outputs / report, "\n".join(lines) + "\n")
    project.append("reviews", {"id": f"R{number}", "ts": now(), "changed": impact["changed"], "downstream": impact["downstream"], "options": impact["options"], "paragraphs": len(impact["paragraphs"]), "leader_before": before["ranking"][0] if before else None, "leader_after": after["ranking"][0] if after else None, "leader_changed": leader_changed, "note": note, "report": report, "fallen": why["chains"], "held": why["held"], "total": why["total"], "params_changed": why["params"]})
    project.event("review", number=number, leader_changed=leader_changed)
    workflow.open_gate(project, "R", f"Подробности: outputs/{report}")
    return {**impact, "report": f"outputs/{report}", "leader_changed": leader_changed, "fallen": why, "ranking": after["ranking"] if after else None}


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
    for field in ("what", "owner", "due", "metric", "threshold", "expected", "review_if"):
        if not data.get(field):
            raise OsnError(f"У обязательства нет поля {field}.")
    claims = project.current("claims")
    bad = [c for c in data.get("claims", []) if c not in claims]
    if bad:
        raise OsnError(f"Нет утверждений: {', '.join(bad)}.")
    record = {"id": project.next_id("commitments", "k"), "ts": now(), "status": "active", "observations": [], "check_at": data.get("check_at") or data["due"], **data}
    project.append("commitments", record)
    project.event("commitment", id=record["id"])
    return record


def close_commitment(project: Project, commitment_id: str, outcome: str, hit: bool) -> dict:
    """Журнал решения: что ожидали при G4 и что вышло. Без этого команда не учится на своих ставках."""
    current = project.current("commitments")
    if commitment_id not in current:
        raise OsnError(f"Обязательство {commitment_id} не найдено.")
    if not outcome.strip():
        raise OsnError("Опишите фактический итог.")
    record = {**current[commitment_id], "status": "closed", "outcome": outcome.strip(), "hit": hit, "closed_at": now(), "ts": now()}
    project.append("commitments", record)
    project.event("commitment_closed", id=commitment_id, hit=hit)
    return record


def track_record(project: Project) -> dict:
    closed = [c for c in project.current("commitments").values() if c["status"] == "closed"]
    hits = sum(1 for c in closed if c.get("hit"))
    return {"closed": len(closed), "hit": hits, "rows": [{"id": c["id"], "expected": c.get("expected"), "outcome": c.get("outcome"), "hit": c.get("hit")} for c in closed]}


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
    soon = sorted(c["id"] for c in claims.values() if c["status"] == "verified" and c.get("expires") and 0 <= (date.fromisoformat(c["expires"]) - today).days <= 14)
    expired = sorted(c["id"] for c in claims.values() if c["status"] == "verified" and claims_mod.expired(c, today.isoformat()))
    if expired:
        items.append({"priority": 1, "kind": "expired", "text": f"Истёк срок годности фактов: {', '.join(expired)}. Найдите свежий источник и замените: claim supersede."})
    if soon:
        items.append({"priority": 3, "kind": "expires", "text": f"Скоро истечёт срок фактов: {', '.join(soon)}."})
    for item in project.current("commitments").values():
        if item["status"] != "active":
            continue
        days = (date.fromisoformat(item.get("check_at") or item["due"]) - today).days
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
