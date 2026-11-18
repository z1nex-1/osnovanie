"""Индекс обоснованности 0-100: сколько в стратегии держится на проверенном и сколько решил человек."""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from . import checks, claims as claims_mod, workflow
from .store import Project, atomic_write

DOCS = ["product.md", "communication.md", "validation.md", "monday.md", "strategy.md"]
HUMAN_CHANNELS = {"chat", "tty"}
WEAK_TYPES = {"hypothesis", "forecast", "assumption"}
WEIGHTS = [
    ("numbers", "Числа стратегии со ссылкой на живое утверждение", 25),
    ("quotes", "Факты и мнения с проверенной цитатой", 20),
    ("fresh", "Утверждения со сроком, который не истёк", 10),
    ("weak", "Гипотезы, прогнозы и допущения с confidence и планом проверки", 10),
    ("objections", "Варианты с возражением skeptic и не менее чем двумя возражениями", 10),
    ("close", "Среди вариантов есть «закрыть направление»", 5),
    ("human", "Точки закрыты человеком, а не сценарием", 15),
    ("review", "Был хотя бы один пересмотр при новых данных", 5),
]
FORMULA = "score = сумма по восьми компонентам: вес × доля выполненного. " + "; ".join(f"{label} — {weight}" for _, label, weight in WEIGHTS) + ". Все доли считаются по текущему состоянию проекта, устаревшие версии утверждений не учитываются."


def _share(done: int, total: int) -> float:
    return done / total if total else 0.0


def _live(claims: dict[str, dict]) -> list[dict]:
    return [c for c in claims.values() if not c.get("superseded_by") and c["status"] != "retracted"]


def _numbers(project: Project, claims: dict[str, dict]) -> tuple[int, int]:
    alive = {c["id"] for c in claims.values() if claims_mod.effective_type(c) not in {"stale", "retracted", "unverified"}}
    total = linked = 0
    for name in DOCS:
        path = project.outputs / name
        if not path.exists():
            continue
        for paragraph in checks.paragraphs(path.read_text("utf-8")):
            count = len(checks.NUM.findall(paragraph))
            total += count
            if set(claims_mod.refs_in(paragraph)) & alive:
                linked += count
    return linked, total


def compute(project: Project) -> dict:
    claims = project.current("claims")
    live = _live(claims)
    notes: list[str] = []
    linked, total = _numbers(project, claims)
    sourced = [c for c in live if c["type"] in claims_mod.NEEDS_SOURCE]
    verified = [c for c in sourced if c["status"] == "verified"]
    with_expiry = [c for c in live if c.get("expires")]
    fresh = [c for c in with_expiry if not claims_mod.expired(c)]
    weak = [c for c in live if c["type"] in WEAK_TYPES]
    weak_ok = [c for c in weak if c.get("confidence") is not None and c.get("verify_plan")]
    options = list(project.current("options").values())
    argued = [o for o in options if len(o.get("objections", [])) >= 2 and any(x.get("by") == "skeptic" for x in o["objections"])]
    gates = workflow.load(project)["gates"]
    closed = {g: i for g, i in gates.items() if i.get("status") == "approved"}
    human = [g for g, i in closed.items() if i.get("channel") in HUMAN_CHANNELS]
    scripted = sorted({i.get("channel") for g, i in closed.items() if i.get("channel") not in HUMAN_CHANNELS})
    if scripted:
        notes.append(f"Точки {', '.join(g for g, i in closed.items() if i.get('channel') not in HUMAN_CHANNELS)} закрыты каналом {', '.join(scripted)}: это сценарий, а не человек, баллы за них не начисляются.")
    needed = 4 + (1 if "R" in gates else 0)
    values = {
        "numbers": (_share(linked, total), f"{linked} из {total} чисел с живой ссылкой"),
        "quotes": (_share(len(verified), len(sourced)), f"{len(verified)} из {len(sourced)} проверены по тексту источника"),
        "fresh": (_share(len(fresh), len(with_expiry)), f"{len(fresh)} из {len(with_expiry)} со сроком не истекли"),
        "weak": (_share(len(weak_ok), len(weak)), f"{len(weak_ok)} из {len(weak)} с confidence и планом проверки"),
        "objections": (_share(len(argued), len(options)), f"{len(argued)} из {len(options)} вариантов"),
        "close": (1.0 if any(o.get("kind") == "close" for o in options) else 0.0, "есть вариант «закрыть»" if any(o.get("kind") == "close" for o in options) else "варианта «закрыть» нет"),
        "human": (_share(len(human), needed), f"{len(human)} из {needed} точек закрыл человек"),
        "review": (1.0 if project.records("reviews") else 0.0, f"пересмотров: {len(project.records('reviews'))}"),
    }
    parts = []
    for key, label, weight in WEIGHTS:
        value, detail = values[key]
        parts.append({"key": key, "label": label, "weight": weight, "value": round(value, 3), "points": round(weight * value, 1), "detail": detail})
    return {"score": round(sum(p["points"] for p in parts)), "max": 100, "parts": parts, "notes": notes, "formula": FORMULA, "reference": bool(project.meta().get("reference"))}


def badge(score: int) -> str:
    color = "#0CDC78" if score >= 80 else "#FFA717" if score >= 60 else "#FF5A64"
    label, message = "обоснованность", f"{score}/100"
    left, right = int(len(label) * 6.5 + 10), int(len(message) * 6.5 + 10)
    width = left + right
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" aria-label="{escape(label)}: {escape(message)}">'
        f'<title>{escape(label)}: {escape(message)}</title>'
        f'<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{width}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{left}" height="20" fill="#07100C"/><rect x="{left}" width="{right}" height="20" fill="{color}"/><rect width="{width}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{left / 2}" y="14">{escape(label)}</text>'
        f'<text x="{left + right / 2}" y="14" fill="#07100C" font-weight="bold">{escape(message)}</text></g></svg>\n'
    )


def write_badge(result: dict, path: Path) -> Path:
    atomic_write(path, badge(result["score"]))
    return path


def render(result: dict) -> str:
    lines = [f"Индекс обоснованности: {result['score']} из 100"]
    for p in result["parts"]:
        lines.append(f"  {p['points']:>5} / {p['weight']:<3} {p['label']} ({p['detail']})")
    lines += [f"Примечание: {n}" for n in result["notes"]]
    return "\n".join(lines)
