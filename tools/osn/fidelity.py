"""Верность пересказа: утверждение не искажает цитату, абзац стратегии не искажает утверждения.

Сверка механическая, без модели. Она ловит три вида искажений, которые чаще всего делает
модель при пересказе: число не то, что в цитате; потерянная оговорка («около», «до», «по оценке»,
«планирует»); допущение или прогноз, поданные в стратегии как факт.
"""
from __future__ import annotations

import re

from . import checks, claims as claims_mod
from .store import Project

DOCS = ("situation.md", "product.md", "communication.md", "validation.md", "monday.md")
NUMBER = re.compile(r"(?<![\w.])\d[\d\s ]*(?:[.,]\d+)?")
# оговорка в цитате → как её можно сохранить в пересказе
HEDGES = {
    "около": r"около|примерно|порядка|~|≈|приблизительно",
    "примерно": r"около|примерно|порядка|~|≈|приблизительно",
    "до ": r"\bдо\b|не более|максимум|не больше",
    "не более": r"\bдо\b|не более|максимум|не больше",
    "более": r"более|свыше|больше|от\b|не менее",
    "свыше": r"более|свыше|больше|от\b|не менее",
    "не менее": r"не менее|от\b|более|свыше|минимум",
    "по оценке": r"оценк|по данным|по мнению|прогноз|ожида",
    "по прогноз": r"прогноз|ожида|оценк",
    "ожида": r"ожида|прогноз|план|оценк",
    "планиру": r"план|намер|собира|ожида",
    "может": r"мож|возможн|способ",
    "могут": r"мож|возможн|способ",
    "в среднем": r"в среднем|средн|медиан",
    "бета": r"бета|тест|пилот",
    "тестов": r"тест|бета|пилот",
}
SOFT = re.compile(r"допущ|гипотез|прогноз|предпол|если\b|ожида|провер|оценк|по модели|сценари|не знаем|пилот|расхо[дж]|не сравнива", re.I)
LABEL = {"assumption": "допущение", "hypothesis": "гипотеза", "forecast": "прогноз", "synthetic": "синтетика", "opinion": "мнение"}


def _nums(text: str) -> list[str]:
    found = []
    for raw in NUMBER.findall(text):
        value = re.sub(r"[\s ]", "", raw).rstrip(".,").replace(",", ".")
        if len(value.replace(".", "")) >= 2:
            found.append(value)
    return found


def _flat(text: str) -> str:
    return re.sub(r"[\s ]", "", text).replace(",", ".")


def claim_problems(claims: dict[str, dict]) -> list[dict]:
    rows = []
    for claim in claims.values():
        quote = claim.get("quote") or ""
        if not quote or claim.get("status") == "retracted" or claim.get("superseded_by") or claim["type"] != "fact":
            continue
        flat_quote = _flat(quote)
        missing = [n for n in _nums(claim["text"]) if n not in flat_quote]
        if missing:
            rows.append({"level": "error", "where": claim["id"], "what": f"числа {', '.join(missing)} нет в цитате"})
        low_quote, low_text = quote.lower(), claim["text"].lower()
        for marker, keep in HEDGES.items():
            if marker in low_quote and not re.search(keep, low_text):
                rows.append({"level": "warning", "where": claim["id"], "what": f"в цитате оговорка «{marker.strip()}», в утверждении её нет"})
                break
        stems = {w[:5] for w in re.findall(r"[а-яё]{6,}", low_text)}
        extra = sorted(st for st in stems if st not in low_quote)
        if len(quote.split()) < 10 and stems and len(extra) * 2 > len(stems):
            rows.append({"level": "warning", "where": claim["id"], "what": f"цитата короче вывода: слов {', '.join(extra[:4])}… в ней нет"})
        if low_quote.count("не ") > 0 and low_text.count("не ") == 0 and re.search(r"\bне\s+(?:является|входит|включ|доступ|предоставл|планиру)", low_quote):
            rows.append({"level": "warning", "where": claim["id"], "what": "в цитате отрицание, в утверждении его нет"})
    return rows


def paragraph_problems(project: Project, claims: dict[str, dict]) -> list[dict]:
    rows = []
    calcs = " ".join(_flat(str(c)) for c in project.records("calcs")) if (project.state / "calcs.jsonl").exists() else ""
    for name in DOCS:
        path = project.outputs / name
        if not path.exists():
            continue
        for paragraph in checks.paragraphs(path.read_text("utf-8")):
            refs = [r for r in claims_mod.refs_in(paragraph) if r in claims]
            if not refs:
                continue
            support = " ".join(_flat(f"{claims[r]['text']} {claims[r].get('quote') or ''} {claims[r].get('value') or ''}") for r in refs)
            clean = re.sub(r"\[c\d{3,}(?:\s*,\s*c\d{3,})*\]", "", paragraph)
            missing = [n for n in _nums(clean) if n not in support and n not in calcs]
            short = " ".join(clean.split())[:90]
            if missing:
                rows.append({"level": "warning", "where": f"{name}: «{short}»", "what": f"числа {', '.join(missing[:4])} нет в опорах {', '.join(refs)}"})
            soft = [r for r in refs if claims[r]["type"] in LABEL]
            if soft and not SOFT.search(clean):
                kinds = sorted({LABEL[claims[r]['type']] for r in soft})
                rows.append({"level": "error", "where": f"{name}: «{short}»", "what": f"опора — {', '.join(kinds)} ({', '.join(soft)}), а в тексте это подано как факт"})
    return rows


def run(project: Project) -> dict:
    claims = project.current("claims")
    rows = claim_problems(claims) + paragraph_problems(project, claims)
    errors = [r for r in rows if r["level"] == "error"]
    checked = sum(1 for c in claims.values() if c["type"] == "fact" and c.get("quote") and not c.get("superseded_by") and c.get("status") != "retracted")
    return {"facts_checked": checked, "errors": len(errors), "warnings": len(rows) - len(errors), "rows": rows}


def problems(project: Project) -> list[str]:
    """Ошибки верности пересказа — для проверки этапа стратегии (блокируют G4)."""
    return [f"верность пересказа, {r['where']}: {r['what']}" for r in run(project)["rows"] if r["level"] == "error"]


def render(result: dict) -> str:
    lines = [f"Фактов с цитатой проверено: {result['facts_checked']}. Ошибок: {result['errors']}, предупреждений: {result['warnings']}."]
    for r in result["rows"]:
        lines.append(f"{'ОШИБКА' if r['level'] == 'error' else 'внимание'}  {r['where']}: {r['what']}")
    return "\n".join(lines)
