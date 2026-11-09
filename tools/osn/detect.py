from __future__ import annotations

import base64
import re
import unicodedata

ZERO_WIDTH = re.compile("[​-‏‪-‮⁠-⁤﻿­]")
LATIN_LOOKALIKE = "aceopxyABCEHKMOPTXY"
CYRILLIC_LOOKALIKE = "асеорхуАВСЕНКМОРТХУ"
TO_CYRILLIC = str.maketrans(LATIN_LOOKALIKE, CYRILLIC_LOOKALIKE)
TO_LATIN = str.maketrans(CYRILLIC_LOOKALIKE, LATIN_LOOKALIKE)
BASE64_BLOB = re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}")

STRONG = [
    ("override-en", r"(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your|system)\b.{0,30}\b(instruction|prompt|rule|message|direction)"),
    ("override-ru", r"(игнорируй|игнорируйте|забудь|забудьте|проигнорируй|отмени|не\s+учитывай|не\s+следуй)\b.{0,50}\b(инструкц|правил|указани|промпт|запрет|ограничени)"),
    ("override-ru2", r"(предыдущ|прежн|вышеуказанн)\w*\s+(инструкц|правил|указани)\w*.{0,40}(недействительн|отмен|игнор|не\s+действ)"),
    ("role-en", r"\byou\s+are\s+now\b|\bact\s+as\s+(an?\s+)?(admin|system|root|developer)|\bnew\s+instructions?\s*:|\bsystem\s+prompt\b|\bdeveloper\s+mode\b"),
    ("role-ru", r"\bты\s+теперь\b|\bтеперь\s+ты\b|\bсистемн\w+\s+(промпт|сообщени|инструкц)|\bновые\s+инструкции\b|\bрежим\s+разработчика\b"),
    ("tags", r"</?\s*(system|assistant|instructions?|tool_call|function_calls?)\s*>|\[/?INST\]|<<\s*SYS\s*>>"),
    ("address-agent-ru", r"\b(агент|нейросет\w*|ассистент|модель|ии|языков\w+\s+модел\w*)\s*[,:!]\s*(обязательно\s+)?(пометь|отметь|удали|измени|запиши|выполни|отправь|игнорируй|считай|добавь|поставь|установи|скрой)"),
    ("address-agent-en", r"\b(ai|agent|assistant|llm|model|claude|gpt)\s*[,:!]\s*(please\s+)?(mark|delete|remove|change|write|run|execute|send|ignore|treat|add|set|hide)\b"),
    ("state-tamper-ru", r"(пометь|отметь|запиши|считай|признай|зафиксируй)\w*\b.{0,60}\b(как|в\s+качестве)\s+(факт|проверенн|подтвержд|verified)"),
    ("state-tamper-en", r"\b(mark|record|treat|flag)\b.{0,60}\bas\s+(a\s+)?(verified|fact|confirmed)\b"),
    ("option-tamper", r"(удали|убери|исключи|скрой)\w*\b.{0,30}\b(вариант|возражени|вывод|опцию|рекомендаци)|\b(delete|remove|drop|hide)\b.{0,30}\b(option|objection|finding)\b"),
    ("recommend-force", r"(рекоменд\w+|выбери|выбирай|предпочти)\b.{0,40}\b(только|обязательно|всегда)\b.{0,40}\b(этот|наш|вариант|продукт)|\b(always|only)\s+recommend\b"),
    ("exec", r"\b(curl|wget|nc|ssh|scp)\s+[-\w]*\s*(https?://|\S+@)|\brm\s+-rf\b|\|\s*(sh|bash|zsh)\b|\bbase64\s+-d\b"),
    ("exec-ru", r"(выполни|запусти|исполни)\w*\s+(команд|скрипт|следующ\w+\s+команд)"),
    ("exfil", r"(отправь|перешли|вышли|загрузи|опубликуй|send|upload|post|exfiltrate|forward)\b.{0,60}(\.env|ключ|токен|секрет|парол|credentials|api[_ -]?key|secret|password)"),
    ("targets", r"AGENTS\.md|CLAUDE\.md|claims\.jsonl|\.claude/|settings\.json|\.cursor/rules|\bgate\s+approve\b"),
    ("gate-forge", r"\bG[1-4]\s+(да|ок|утверждаю|ok|approve)\b|(человек|пользователь|руководитель)\s+(уже\s+)?(подтвердил|утвердил|одобрил|разрешил)|\b(user|human)\s+(has\s+)?(already\s+)?(approved|confirmed|authorized)"),
    ("reviewer-address", r"\b(llm|ai)[\s-]+(reviewer|judge|grader|evaluator)\b|\b(give|award|rate)\b.{0,40}\b\d{1,3}\s*(/|out of)\s*\d{1,3}\b|(проверяющ\w+|ии-эксперт\w*|жюри)\s*[:,].{0,60}(поставь|оцени|присуди)"),
    ("judge-bait", r"(поставь|поставьте|выстави|присвой)\w*\s+.{0,30}(максимальн|высш\w+|наивысш\w+)\s+(балл|оценк)|\b(give|award)\b.{0,30}\b(maximum|highest|full|top)\s+(score|marks|rating)"),
]
WEAK = [
    ("imperative-en", r"\byou\s+must\b|\bdo\s+not\s+tell\b|\bdon'?t\s+mention\b|\bimportant\s+note\s+to\b"),
    ("imperative-ru", r"\bне\s+сообщай\b|\bне\s+упоминай\b|\bты\s+должен\b|\bвы\s+обязаны\s+сообщить\b"),
    ("url-params", r"https?://\S+\?\S*(key|token|data|secret)="),
]
_STRONG = [(name, re.compile(rx, re.I | re.S)) for name, rx in STRONG]
_WEAK = [(name, re.compile(rx, re.I | re.S)) for name, rx in WEAK]


def normalize_for_scan(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    cleaned = unicodedata.normalize("NFKC", text)
    without = ZERO_WIDTH.sub("", cleaned)
    if without != cleaned:
        flags.append("zero-width")
    # Разрядка «и г н о р и р у й» обходит обычные шаблоны.
    collapsed = re.sub(r"(?<=\b\w) (?=\w\b)", "", without)
    return collapsed, flags


def _mixed_script_fold(text: str) -> str:
    def fold(match: re.Match) -> str:
        word = match.group(0)
        cyr = len(re.findall("[а-яёА-ЯЁ]", word))
        lat = len(re.findall("[a-zA-Z]", word))
        if not (cyr and lat):
            return word
        return word.translate(TO_LATIN if lat >= cyr else TO_CYRILLIC)

    return re.sub(r"[^\W\d_]+", fold, text)


def _hits(text: str) -> tuple[list[str], list[str]]:
    strong = [name for name, rx in _STRONG if rx.search(text)]
    weak = [name for name, rx in _WEAK if rx.search(text)]
    return strong, weak


def scan(text: str) -> dict:
    normalized, flags = normalize_for_scan(text)
    folded = _mixed_script_fold(normalized)
    strong, weak = _hits(normalized)
    extra_strong, extra_weak = _hits(folded)
    strong = sorted(set(strong) | set(extra_strong))
    weak = sorted(set(weak) | set(extra_weak))
    if folded != normalized and strong:
        flags.append("homoglyph")
    for blob in BASE64_BLOB.findall(normalized):
        try:
            decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        inner, _ = _hits(normalize_for_scan(decoded)[0])
        if inner:
            strong = sorted(set(strong) | {f"base64:{n}" for n in inner})
    return {"suspicious": bool(strong), "strong": strong, "weak": weak, "flags": flags}


def segments(text: str) -> list[str]:
    return [part for part in re.split(r"\n\s*\n|(?<=\n)(?=\S)", text) if part.strip()]
