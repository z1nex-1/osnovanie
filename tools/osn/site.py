"""Пакет для сайта: один project.json схемы osnovanie-site/1 и раскладка графа, посчитанная заранее."""
from __future__ import annotations

import math
import random
from pathlib import Path
from urllib.parse import urlparse

from . import checks, claims as claims_mod, cost, model, score as score_mod, whatif, workflow
from .store import Project, now, read_json, write_json

SCHEMA = "osnovanie-site/1"
BINS = 24
ITERATIONS = 400
DOCS = ["situation.md", "product.md", "communication.md", "validation.md", "monday.md", "strategy.md"]


def layout(nodes: list[str], edges: list[tuple[str, str]], seed: str = "osnovanie-site") -> dict[str, tuple[float, float, float]]:
    """Фрухтерман-Рейнгольд в трёх измерениях; seed фиксирован, поэтому тот же проект даёт ту же картинку."""
    rng = random.Random(seed)
    count = len(nodes)
    if count == 0:
        return {}
    pos = {n: [rng.uniform(-1, 1) for _ in range(3)] for n in nodes}
    k = math.sqrt(8.0 / count)
    temperature = 0.5
    index = {n: i for i, n in enumerate(nodes)}
    pairs = [(index[a], index[b]) for a, b in edges if a in index and b in index and a != b]
    coords = [pos[n] for n in nodes]
    for step in range(ITERATIONS):
        force = [[0.0, 0.0, 0.0] for _ in range(count)]
        for i in range(count):
            for j in range(i + 1, count):
                d = [coords[i][a] - coords[j][a] for a in range(3)]
                dist = math.sqrt(sum(x * x for x in d)) or 1e-6
                push = k * k / dist / dist
                for a in range(3):
                    force[i][a] += d[a] * push
                    force[j][a] -= d[a] * push
        for i, j in pairs:
            d = [coords[i][a] - coords[j][a] for a in range(3)]
            dist = math.sqrt(sum(x * x for x in d)) or 1e-6
            pull = dist / k
            for a in range(3):
                force[i][a] -= d[a] * pull
                force[j][a] += d[a] * pull
        for i in range(count):
            for a in range(3):
                force[i][a] -= coords[i][a] * 0.6
            mag = math.sqrt(sum(x * x for x in force[i])) or 1e-6
            move = min(mag, temperature)
            for a in range(3):
                coords[i][a] += force[i][a] / mag * move
        temperature = max(0.005, temperature * (1 - (step + 1) / ITERATIONS) ** 0.1 * 0.985)
    center = [sum(c[a] for c in coords) / count for a in range(3)]
    shifted = [[c[a] - center[a] for a in range(3)] for c in coords]
    scale = max((abs(v) for c in shifted for v in c), default=1.0) or 1.0
    return {n: tuple(round(v / scale, 4) for v in shifted[i]) for n, i in index.items()}


def _histogram(values: list[float]) -> dict:
    low, high = min(values), max(values)
    if low == high:
        return {"edges": [low, high], "counts": [len(values)]}
    width = (high - low) / BINS
    counts = [0] * BINS
    for v in values:
        counts[min(int((v - low) / width), BINS - 1)] += 1
    return {"edges": [round(low + width * i, 2) for i in range(BINS + 1)], "counts": counts}


def _model_part(project: Project) -> dict | None:
    mdl = read_json(project.state / "model.json")
    runs = project.records("model_runs")
    if not mdl or not runs:
        return None
    params = model._resolve(project, mdl)
    constants = {k: v["value"] for k, v in mdl.get("constants", {}).items()}
    base, results, ranking, samples = model.simulate(mdl, params, constants, keep_samples=True)
    last = runs[-1]
    return {
        "output_unit": mdl["output_unit"], "samples": model.SAMPLES, "formulas": mdl["options"], "constants": mdl.get("constants", {}),
        "params": last["params"], "ranking": ranking, "assumption_share": last["assumption_share"], "run": last["id"],
        "monte_carlo": {o: {**results[o], "histogram": _histogram(samples[o])} for o in ranking},
        "breakeven": last["breakeven"],
        "history": [{"id": r["id"], "ts": r["ts"], "tag": r["tag"], "ranking": r["ranking"], "results": r["results"]} for r in runs],
    }


def _gates(project: Project) -> list[dict]:
    state = workflow.load(project)
    rows = []
    for gate, info in state["gates"].items():
        channel = info.get("channel")
        rows.append({"gate": gate, "label": workflow.GATE_LABEL.get(gate, gate), "status": info.get("status"), "channel": channel, "by_human": channel in score_mod.HUMAN_CHANNELS, "opened_at": info.get("opened_at"), "closed_at": info.get("closed_at"), "round": info.get("round"), "comment": info.get("comment", ""), "options": info.get("options", []), "owner": info.get("owner")})
    return rows


def _waves(claims: dict[str, dict], reviews: list[dict]) -> list[dict]:
    out = []
    for review in reviews:
        waves = whatif.layers(claims, set(review["changed"]))
        out.append({**review, "waves": [{"depth": i + 1, "claims": ids} for i, ids in enumerate(waves)]})
    return out


def build(project: Project) -> dict:
    claims = project.current("claims")
    sources = project.current("sources")
    options = project.current("options")
    meta = project.meta()
    state = workflow.load(project)
    claim_rows = []
    for claim in sorted(claims.values(), key=lambda c: c["id"]):
        source = sources.get(claim.get("source_id") or "", {})
        claim_rows.append({
            "id": claim["id"], "type": claim["type"], "effective_type": claims_mod.effective_type(claim), "status": claim["status"],
            "confidence": claim.get("confidence"), "text": claim["text"], "quote": claim.get("quote"), "source_id": claim.get("source_id"),
            "source": {"title": source.get("title"), "origin": source.get("origin"), "domain": urlparse(source.get("origin", "")).hostname, "published_at": source.get("published_at")} if source else None,
            "value": claim.get("value"), "unit": claim.get("unit"), "topic": claim.get("topic"), "range": claim.get("range"),
            "created_at": claim.get("created_at"), "updated_at": claim.get("updated_at"), "expires": claim.get("expires"),
            "depends_on": claim.get("depends_on", []), "superseded_by": claim.get("superseded_by"), "supersedes": claim.get("supersedes"),
            "verify_plan": claim.get("verify_plan"), "resolution": claim.get("resolution"), "conflict_between": claim.get("conflict_between"),
            "calc_id": claim.get("calc_id"), "author": claim.get("author"), "stale_reason": claim.get("stale_reason"),
        })
    edges = [{"from": dep, "to": c["id"], "kind": "depends_on"} for c in claim_rows for dep in c["depends_on"] if dep in claims]
    edges += [{"from": c["source_id"], "to": c["id"], "kind": "cites"} for c in claim_rows if c["source_id"] in sources]
    for option in options.values():
        edges += [{"from": b, "to": option["id"], "kind": "basis"} for b in option.get("basis", []) if b in claims]
        for objection in option.get("objections", []):
            edges += [{"from": c, "to": option["id"], "kind": "objection"} for c in objection.get("claims", []) if c in claims]
    doc_refs = []
    for doc in DOCS:
        path = project.outputs / doc
        if path.exists():
            for number, paragraph in enumerate(checks.paragraphs(path.read_text("utf-8")), 1):
                for ref in claims_mod.refs_in(paragraph):
                    if ref in claims:
                        doc_refs.append({"doc": doc, "paragraph": number, "claim": ref})
    source_rows = []
    for source in sorted(sources.values(), key=lambda s: s["id"]):
        source_rows.append({"id": source["id"], "title": source["title"], "kind": source["kind"], "origin": source["origin"], "domain": urlparse(source["origin"]).hostname, "sha256": source["sha256"], "published_at": source.get("published_at"), "accessed_at": source.get("accessed_at"), "offline": bool(source.get("offline")), "chars": source.get("chars"), "quarantined": source.get("quarantined", 0), "status": source.get("status")})
    option_rows = [{k: v for k, v in o.items() if k not in {"ts"}} for o in sorted(options.values(), key=lambda o: o["id"])]

    node_ids = [c["id"] for c in claim_rows] + [s["id"] for s in source_rows] + [o["id"] for o in option_rows]
    kinds = {**{c["id"]: "claim" for c in claim_rows}, **{s["id"]: "source" for s in source_rows}, **{o["id"]: "option" for o in option_rows}}
    positions = layout(node_ids, [(e["from"], e["to"]) for e in edges])
    questions = [{k: q.get(k) for k in ("id", "text", "status", "why", "changes", "claims")} for q in sorted(project.current("questions").values(), key=lambda q: q["id"])]
    return {
        "schema": SCHEMA, "generated_at": now(),
        "project": {"slug": project.slug, "title": meta.get("title"), "question": meta.get("question"), "reference": bool(meta.get("reference")), "note": meta.get("note"), "stage": state["stage"], "stage_label": workflow.STAGE_LABEL[state["stage"]], "decision": meta.get("decision"), "created_at": meta.get("created_at")},
        "brief": read_json(project.state / "brief.json", {}),
        "claims": claim_rows, "edges": edges, "doc_refs": doc_refs, "sources": source_rows, "options": option_rows,
        "model": _model_part(project), "questions": questions, "human_points": _gates(project),
        "events": project.records("events"), "reviews": _waves(claims, project.records("reviews")),
        "commitments": sorted(project.current("commitments").values(), key=lambda c: c["id"]),
        "cost": cost.summary(project), "score": score_mod.compute(project),
        "layout": {"algorithm": "fruchterman-reingold-3d", "seed": "osnovanie-site", "nodes": [{"id": n, "kind": kinds[n], "x": positions[n][0], "y": positions[n][1], "z": positions[n][2]} for n in node_ids]},
    }


def export(project: Project, out: Path) -> dict:
    data = build(project)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "project.json"
    write_json(path, data)
    return {"out": str(path), "schema": SCHEMA, "claims": len(data["claims"]), "edges": len(data["edges"]), "sources": len(data["sources"]), "events": len(data["events"]), "nodes": len(data["layout"]["nodes"]), "kb": path.stat().st_size // 1024}
