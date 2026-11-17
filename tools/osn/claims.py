from __future__ import annotations

import re

from . import detect
from .store import OsnError, PolicyError, Project, now

TYPES = ("fact", "calculation", "hypothesis", "forecast", "opinion", "assumption", "synthetic")
NEEDS_SOURCE = {"fact", "opinion"}
# Порядок слабости важен для карты доверия: абзац окрашивается по самому слабому основанию.
STRENGTH = ["fact", "calculation", "opinion", "assumption", "forecast", "hypothesis", "synthetic", "unverified", "stale", "retracted"]
LABEL = {
    "fact": "факт", "calculation": "расчёт", "hypothesis": "гипотеза", "forecast": "прогноз", "opinion": "мнение",
    "assumption": "допущение", "synthetic": "модельный респондент", "unverified": "не проверено", "stale": "устарело", "retracted": "отозвано",
}


def expired(claim: dict, today: str | None = None) -> bool:
    # Цена конкурента или доля рынка верны на дату; после expires факт снова требует проверки.
    until = claim.get("expires")
    return bool(until) and until < (today or now()[:10])


def effective_type(claim: dict) -> str:
    if claim["status"] == "retracted":
        return "retracted"
    if claim.get("superseded_by"):
        return "stale"
    if claim["status"] == "stale" or expired(claim):
        return "stale"
    if claim["type"] in {"fact", "opinion", "calculation"} and claim["status"] != "verified":
        return "unverified"
    return claim["type"]


def _check_text(text: str) -> None:
    verdict = detect.scan(text)
    if verdict["suspicious"]:
        raise PolicyError(f"Текст похож на команду ({', '.join(verdict['strong'])}), утверждение не записано.")


def add(project: Project, data: dict, author: str = "agent") -> dict:
    text = (data.get("text") or "").strip()
    kind = data.get("type")
    if not text:
        raise OsnError("У утверждения нет текста.")
    if kind not in TYPES:
        raise OsnError(f"Тип утверждения: {', '.join(TYPES)}.")
    _check_text(text)
    source_id, quote = data.get("source_id"), (data.get("quote") or "").strip()
    if kind in NEEDS_SOURCE and not (source_id and quote):
        raise PolicyError(f"Тип {kind} требует источник и дословную цитату. Иначе запишите как hypothesis.")
    if quote:
        _check_text(quote)
    confidence = data.get("confidence")
    if confidence is not None and not (isinstance(confidence, (int, float)) and 0 <= confidence <= 1):
        raise OsnError("confidence — число от 0 до 1: насколько вывод выдержит проверку.")
    expires = data.get("expires")
    if expires and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(expires)):
        raise OsnError("expires — дата ГГГГ-ММ-ДД, после которой факт надо проверить заново.")
    if kind == "calculation" and not data.get("calc_id"):
        raise PolicyError("Расчёт записывается только через osnovanie calc или model, у него должен быть calc_id.")
    with project.lock():
        current = project.current("claims")
        if source_id and source_id not in project.current("sources"):
            raise OsnError(f"Источник {source_id} не найден.")
        for existing in current.values():
            if existing["text"] == text and existing.get("source_id") == source_id and existing["status"] != "retracted":
                return {**existing, "duplicate": True}
        for dep in data.get("depends_on", []):
            if dep not in current:
                raise OsnError(f"depends_on ссылается на несуществующее утверждение {dep}.")
        claim_id = project.next_id("claims", "c")
        record = {
            "id": claim_id, "rev": 1, "text": text, "type": kind, "status": "unverified",
            "source_id": source_id, "quote": quote or None, "locator": data.get("locator"),
            "value": data.get("value"), "unit": data.get("unit"), "period": data.get("period"),
            "depends_on": data.get("depends_on", []), "confidence": data.get("confidence"),
            "supersedes": data.get("supersedes"), "superseded_by": None, "verified_by": None,
            "calc_id": data.get("calc_id"), "author": author, "stage": project.meta().get("stage_hint"), "created_at": now(),
            "topic": data.get("topic"), "expires": expires,
        }
        if kind in {"hypothesis", "forecast", "assumption", "synthetic"}:
            record["status"] = "open"
        if kind == "calculation":
            record["status"] = "verified"
            record["verified_by"] = f"calc:{data['calc_id']}"
        project.append("claims", record)
        project.event("claim_added", id=claim_id, type=kind)
        return record


def update(project: Project, claim_id: str, **changes) -> dict:
    current = project.current("claims")
    if claim_id not in current:
        raise OsnError(f"Утверждение {claim_id} не найдено.")
    record = {**current[claim_id], **changes, "rev": current[claim_id]["rev"] + 1, "updated_at": now()}
    project.append("claims", record)
    return record


def get(project: Project, claim_id: str) -> dict:
    current = project.current("claims")
    if claim_id not in current:
        raise OsnError(f"Утверждение {claim_id} не найдено.")
    return current[claim_id]


def retract(project: Project, claim_id: str, why: str) -> dict:
    if not why.strip():
        raise OsnError("Укажите причину отзыва.")
    with project.lock():
        record = update(project, claim_id, status="retracted", retract_reason=why)
        project.event("claim_retracted", id=claim_id, why=why)
        return record


def supersede(project: Project, claim_id: str, data: dict) -> dict:
    old = get(project, claim_id)
    data = {**data, "supersedes": claim_id}
    data.setdefault("type", old["type"])
    data.setdefault("depends_on", old.get("depends_on", []))
    for field in ("unit", "period", "topic"):
        data.setdefault(field, old.get(field))
    new = add(project, data)
    with project.lock():
        update(project, claim_id, superseded_by=new["id"])
        project.event("claim_superseded", old=claim_id, new=new["id"])
    return new


def resolve_chain(claims: dict[str, dict], claim_id: str) -> dict:
    seen = set()
    claim = claims[claim_id]
    while claim.get("superseded_by") and claim["id"] not in seen:
        seen.add(claim["id"])
        claim = claims[claim["superseded_by"]]
    return claim


REF = re.compile(r"\[(c\d{3,}(?:\s*,\s*c\d{3,})*)\]")


def refs_in(text: str) -> list[str]:
    found: list[str] = []
    for group in REF.findall(text):
        found += [part.strip() for part in group.split(",")]
    return found
