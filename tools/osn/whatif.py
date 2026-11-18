"""Гипотетический пересмотр: считает последствия, ничего не записывая в проект."""
from __future__ import annotations

from . import checks, claims as claims_mod, model
from .store import OsnError, Project, read_json

DOCS = ["situation.md", "product.md", "communication.md", "validation.md", "monday.md", "strategy.md"]
WIDEN_LOW, WIDEN_HIGH = 0.5, 1.5
SCAN_LOW, SCAN_HIGH = 0.25, 2.0


def _reverse(claims: dict[str, dict]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {}
    for claim in claims.values():
        for dep in claim.get("depends_on", []):
            reverse.setdefault(dep, set()).add(claim["id"])
    return reverse


def layers(claims: dict[str, dict], dropped: set[str]) -> list[list[str]]:
    reverse = _reverse(claims)
    seen = set(dropped)
    frontier = sorted(dropped)
    out: list[list[str]] = []
    while frontier:
        nxt = sorted({child for c in frontier for child in reverse.get(c, ()) if child not in seen})
        seen.update(nxt)
        if nxt:
            out.append(nxt)
        frontier = nxt
    return out


def _paragraphs(project: Project, touched: set[str]) -> list[dict]:
    found = []
    for doc in DOCS:
        path = project.outputs / doc
        if not path.exists():
            continue
        for number, paragraph in enumerate(checks.paragraphs(path.read_text("utf-8")), 1):
            refs = set(claims_mod.refs_in(paragraph)) & touched
            if refs:
                found.append({"doc": doc, "paragraph": number, "refs": sorted(refs), "text": paragraph[:160]})
    return found


def _leader(results: dict, ranking: list[str]) -> dict:
    best = max(sorted(results), key=lambda o: results[o]["p_best"])
    return {"by_base": ranking[0], "by_probability": best, "p_best": results[best]["p_best"]}


def _scan(mdl: dict, params: dict, constants: dict, env: dict, leader: str) -> list[dict]:
    wide = {k: {**p, "low": min(p["low"], p["base"]) * (SCAN_LOW if p["base"] > 0 else SCAN_HIGH), "high": max(p["high"], p["base"]) * (SCAN_HIGH if p["base"] > 0 else SCAN_LOW)} for k, p in params.items()}
    flips = model._breakeven(mdl, wide, constants, env, leader)
    return [f for f in flips if f["threshold"] is not None]


def _model_part(project: Project, touched: set[str], sets: dict[str, float]) -> dict | None:
    mdl = read_json(project.state / "model.json")
    if not mdl:
        return None
    params = model._resolve(project, mdl)
    constants = {k: v["value"] for k, v in mdl.get("constants", {}).items()}
    unknown = [k for k in sets if k not in params]
    if unknown:
        raise OsnError(f"В модели нет величин: {', '.join(unknown)}. Есть: {', '.join(params)}.")
    base, results, ranking, _ = model.simulate(mdl, params, constants)
    after = {k: dict(p) for k, p in params.items()}
    widened = []
    for name, spec in after.items():
        if spec["claim"] in touched:
            spec["low"] = spec["low"] * WIDEN_LOW if spec["low"] > 0 else spec["low"] * WIDEN_HIGH
            spec["high"] = spec["high"] * WIDEN_HIGH if spec["high"] > 0 else spec["high"] * WIDEN_LOW
            spec["claim"] = None
            widened.append(name)
    for name, value in sets.items():
        spec = after[name]
        spec["base"] = value
        spec["low"], spec["high"] = min(spec["low"], value), max(spec["high"], value)
    a_base, a_results, a_ranking, _ = model.simulate(mdl, after, constants)
    env = {**{k: p["base"] for k, p in after.items()}, **constants}
    before_leader, after_leader = _leader(results, ranking), _leader(a_results, a_ranking)
    return {
        "before": {"ranking": ranking, "results": results, "leader": before_leader},
        "after": {"ranking": a_ranking, "results": a_results, "leader": after_leader},
        "widened_params": widened,
        "set_params": sets,
        "leader_changed": before_leader["by_base"] != after_leader["by_base"] or before_leader["by_probability"] != after_leader["by_probability"],
        "breakeven": _scan(mdl, after, constants, env, a_ranking[0]),
        "unit": mdl["output_unit"],
    }


def run(project: Project, drop_sources: list[str], drop_claims: list[str], sets: dict[str, float]) -> dict:
    claims = project.current("claims")
    known_sources = project.current("sources")
    bad_sources = [s for s in drop_sources if s not in known_sources]
    if bad_sources:
        raise OsnError(f"Нет источников: {', '.join(bad_sources)}.")
    bad_claims = [c for c in drop_claims if c not in claims]
    if bad_claims:
        raise OsnError(f"Нет утверждений: {', '.join(bad_claims)}.")
    if not (drop_sources or drop_claims or sets):
        raise OsnError("Нечего проверять: задайте --drop-source, --drop-claim или --set.")
    dropped = set(drop_claims) | {c["id"] for c in claims.values() if c.get("source_id") in drop_sources}
    steps = layers(claims, dropped)
    touched = dropped | {c for step in steps for c in step}

    def card(claim_id: str) -> dict:
        claim = claims[claim_id]
        return {"id": claim_id, "type": claim["type"], "text": claim["text"][:140]}

    return {
        "dropped": {"sources": drop_sources, "claims": sorted(dropped)},
        "dropped_claims": [card(c) for c in sorted(dropped)],
        "layers": [{"depth": i + 1, "claims": [card(c) for c in step]} for i, step in enumerate(steps)],
        "fall": len(touched),
        "paragraphs": _paragraphs(project, touched),
        "model": _model_part(project, touched, sets),
        "writes": False,
    }


def render(result: dict) -> str:
    lines = [f"Что-если (ничего не записано). Убираем: {', '.join(result['dropped']['claims']) or 'ничего'}."]
    for layer in result["layers"]:
        lines.append(f"Слой {layer['depth']}: " + ", ".join(c["id"] for c in layer["claims"]))
    lines.append(f"Теряют основание: {result['fall']} утверждений. Затронуто абзацев: {len(result['paragraphs'])}.")
    for p in result["paragraphs"][:12]:
        lines.append(f"  {p['doc']} №{p['paragraph']}: {p['text'][:90]}")
    part = result["model"]
    if part:
        b, a = part["before"]["leader"], part["after"]["leader"]
        lines.append(f"Лидер модели: было {b['by_base']} (по вероятности {b['by_probability']}, {b['p_best']:.0%}), стало {a['by_base']} (по вероятности {a['by_probability']}, {a['p_best']:.0%}).")
        if part["widened_params"]:
            lines.append("Диапазоны расширены у величин без опоры: " + ", ".join(part["widened_params"]))
        for flip in part["breakeven"]:
            lines.append(f"  лидер меняется на {flip['new_leader']}, если {flip['param']} ({flip['unit']}) дойдёт до {flip['threshold']}")
    return "\n".join(lines)
