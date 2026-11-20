from __future__ import annotations

import re

from . import claims as claims_mod, sources
from .store import Project, now

DASHES = "‐‑‒–—―−"
QUOTES = "\"'«»„“”‘’‚‹›`"
NUMBER = re.compile(r"\d[\d   ]*(?:[.,]\d+)?")
MIN_QUOTE = 12


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    for char in DASHES:
        text = text.replace(char, "-")
    for char in QUOTES:
        text = text.replace(char, "")
    return re.sub(r"\s+", " ", text.replace(" ", " ").replace(" ", " ")).strip()


def _flat(text: str) -> str:
    return re.sub(r"[\s]+", "", normalize(text))


def numbers(text: str) -> set[str]:
    found = set()
    for raw in NUMBER.findall(text):
        value = re.sub(r"[\s  ]", "", raw).replace(",", ".").rstrip(".")
        if value:
            found.add(value)
    return found


def quote_numbers(text: str) -> set[str]:
    """Числа цитаты во всех прочтениях: в строке таблицы из PDF «282 275 279» — это и три числа, и тысячи вида «12 000»."""
    found = set()
    for raw in NUMBER.findall(text):
        groups = [g for g in re.split(r"[\s  ]+", raw.strip()) if g]
        for start in range(len(groups)):
            for end in range(start + 1, len(groups) + 1):
                value = "".join(groups[start:end]).replace(",", ".").rstrip(".")
                if value:
                    found.add(value)
    return found


def check_quote(claim: dict, source_text: str) -> tuple[bool, str]:
    quote = claim.get("quote") or ""
    if len(normalize(quote)) < MIN_QUOTE:
        return False, "цитата слишком короткая, её нельзя проверить"
    if "КАРАНТИН" in quote:
        return False, "цитата указывает на фрагмент в карантине"
    if _flat(quote) not in _flat(source_text):
        return False, "цитата не найдена в сохранённом источнике"
    missing = sorted(numbers(claim["text"]) - quote_numbers(quote))
    if missing:
        return False, f"числа из утверждения отсутствуют в цитате: {', '.join(missing)}"
    return True, "цитата найдена дословно, числа совпадают"


def run(project: Project, judge_entailment: bool = False) -> dict:
    result = {"verified": [], "contested": [], "skipped": 0}
    with project.lock():
        current = project.current("claims")
        for claim in current.values():
            if claim["type"] not in claims_mod.NEEDS_SOURCE or claim["status"] in {"verified", "retracted"}:
                result["skipped"] += 1
                continue
            text = sources.parsed_text(project, claim["source_id"])
            ok, reason = check_quote(claim, text)
            if ok and judge_entailment:
                from . import judge

                verdict = judge.entails(project, claim["text"], claim["quote"])
                if verdict["decision"] != "yes":
                    ok, reason = False, f"судья: цитата не подтверждает утверждение (p={verdict['p']:.2f}, {verdict['backend']})"
            if ok:
                claims_mod.update(project, claim["id"], status="verified", verified_by="verify_quotes", verified_at=now(), verify_note=reason)
                result["verified"].append(claim["id"])
            else:
                claims_mod.update(project, claim["id"], status="contested", verify_note=reason)
                result["contested"].append({"id": claim["id"], "reason": reason})
        project.event("verify", verified=len(result["verified"]), contested=len(result["contested"]))
    return result
