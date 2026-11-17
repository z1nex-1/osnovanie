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
    ("override-en2", r"\b(ignore|disregard|forget)\s+(everything|anything|all)\s+(above|before|preceding|prior)|\b(begin|start)\s+a\s+(fresh|new)\s+(session|conversation|context)\b"),
    ("override-ru", r"(игнорируй|игнорируйте|забудь|забудьте|проигнорируй|отмени|не\s+учитывай|не\s+следуй)\b.{0,50}\b(инструкц|правил|указани|промпт|запрет|ограничени)"),
    ("override-ru3", r"(отмени|сними|отключи|забудь)\w*\s+(\w+\s+){0,2}(ограничени|правил|запрет|инструкц)|\bс\s+этого\s+момента\b.{0,60}\b(ты|вы|агент|ассистент|модель|отмен|игнор|забуд)"),
    ("override-ru2", r"(предыдущ|прежн|вышеуказанн)\w*\s+(инструкц|правил|указани)\w*.{0,40}(недействительн|отмен|игнор|не\s+действ)"),
    ("role-en", r"\byou\s+are\s+(now|no\s+longer)\b|\bfrom\s+(now|this\s+point)(\s+on)?\b.{0,30}\byou\s+(act|are|will|must|should)\b|\b(admin|administrator|developer|system)\s+note\b|\bfull\s+authority\s+over\b|\bact\s+as\s+(an?\s+)?(admin|system|root|developer)|\bnew\s+instructions?\s*:|\bsystem\s+prompt\s*[:=]|\b(new|updated|real)\s+system\s+prompt\b|\bdeveloper\s+mode\s+(is\s+)?(enabled|on|activated)\b.{0,40}\b(assistant|agent|model|restrictions)|\b(restrictions|guardrails|rules|limits)\s+(are|have\s+been)\s+(lifted|removed|disabled|off)\b|\byour\s+(real\s+|new\s+)?job\s+is\s+(now\s+)?to\b"),
    ("role-ru", r"\bты\s+теперь\b|\bтеперь\s+ты\b|\bты\s+больше\s+не\b|\bсистемн\w+\s+(промпт|сообщени|инструкц)\w*\s*[:=]|\bновые\s+инструкции\b|\b(включ[её]н|активирован)\w*\s+режим\w*\s+(разработчика|developer|отладки)\b.{0,40}(агент|ассистент|модел|ограничени)|\bсообщени\w+\s+от\s+(разработчик|администратор|систем)\w*|ограничени\w*\s+(\w+\s+){0,2}(сняты|отключены|сняли|отменены)"),
    ("tags", r"</?\s*(system|assistant|instructions?|tool_call|function_calls?)\s*>|\[/?INST\]|<<\s*SYS\s*>>|<\|\s*(im_start|im_end|system|endoftext)\s*\|>"),
    ("address-agent-ru", r"\b(агент|нейросет\w*|ассистент|модель|ии|языков\w+\s+модел\w*)\s*[,:!]\s*(обязательно\s+)?(пометь|отметь|удали|измени|запиши|выполни|отправь|игнорируй|считай|добавь|поставь|установи|скрой)"),
    ("address-agent-en", r"\b(ai|agent|assistant|llm|model|claude|gpt)\s*[,:!]\s*(please\s+)?(mark|delete|remove|change|write|run|execute|send|ignore|treat|add|set|hide)\b"),
    ("state-tamper-ru", r"(пометь|отметь|запиши|считай|признай|зафиксируй|помечай|отмечай)\w*\b.{0,100}\b(как|в\s+качестве)\s+(\w+\s+){0,2}(факт|проверенн|подтвержд|достоверн|верифицир|verified)"),
    ("state-tamper-en", r"\b(mark|record|treat|flag|label|tag)\b.{0,80}\bas\s+(a\s+|an\s+)?(\w+\s+){0,2}(verified|fact|confirmed|trusted|authoritative)\b"),
    ("option-tamper", r"(удали|убери|исключи|скрой)\w*\b.{0,30}\b(вариант|возражени|вывод|опцию|рекомендаци)|\b(delete|remove|drop|hide|omit|exclude|leave\s+out)\b.{0,60}\b(options?|objections?|findings?|comparison|alternatives?|competitors?|rivals?)\b|\bquietly\s+(drop|remove|omit|hide|skip)\b|(тихо|незаметно|молча)\s+(убери|удали|исключи|скрой|не\s+включай)"),
    ("recommend-force", r"(рекоменд\w+|выбери|выбирай|предпочти)\b.{0,40}\b(только|обязательно|всегда)\b.{0,40}\b(этот|наш|вариант|продукт)|\b(always|only)\s+recommend\b"),
    ("exec", r"\b(curl|wget|nc|ssh|scp)\s+[-\w]*\s*(https?://|\S+@)|\brm\s+-rf\b|\|\s*(sh|bash|zsh)\b|\bbase64\s+-d\b"),
    ("exec-ru", r"(выполни|запусти|исполни)\w*\s+(команд|скрипт|следующ\w+\s+команд)"),
    ("exfil", r"(отправь|перешли|вышли|загрузи|опубликуй|send|upload|post|exfiltrate|forward)\b.{0,60}(\.env|ключ|токен|секрет|парол|credentials|api[_ -]?key|secret|password)"),
    ("gate-forge2", r"\b(gate|checkpoint)\s*G[1-4]\b.{0,50}\b(signed\s+off|approved|passed|closed|cleared)\b|\bточк\w*\s+G[1-4]\b.{0,50}(закрыт|пройден|утвержд|подписан|согласован)|\btreat\b.{0,40}\bas\s+(passed|approved|signed)"),
    ("recommend-force3", r"(всегда|только|обязательно)\s+(рекомендуй|советуй|выбирай|предлагай|пиши)|рекомендаци\w*.{0,40}\bдолжн\w*\s+быть|\bregardless\s+of\s+(the\s+)?(data|numbers|evidence|facts|figures)\b|\bconclude\s+that\b.{0,50}\b(optimal|best|only)\b|(не\s+(упоминай|упоминать|включай|включать|показывай)|избегать)\s+(\w+\s+){0,3}(в\s+)?(вывод|отч[её]т|анализ|рекомендац)"),
    ("recommend-force2", r"\b(final\s+)?(recommendation|conclusion)s?\s+(must|should|has\s+to|is\s+to)\s+be\b|\bwhatever\s+the\s+(data|evidence|numbers?)\s+(says?|shows?)\b|\bdo\s+not\s+(present|offer|mention)\s+(any\s+)?alternatives\b|\bdrop\s+any\s+recommendation|(в\s+(итогов\w+\s+)?вывод\w*|в\s+отч[её]т\w*|в\s+рекомендаци\w*)\s+(обязательно\s+)?(напиши|укажи|сделай|пиши)|единственн\w+\s+(разумн|верн|правильн|возможн)\w*\s+(выбор|вариант|решени)|независимо\s+от\s+(данных|цифр|фактов)"),
    ("exec2", r"\b(open|launch|spawn)\s+a\s+(shell|terminal)\b|(выполни|запусти|исполни)\s+`[^`]+`|\bnc\s+\d{1,3}(\.\d{1,3}){3}\b|\bcat\s+~/|(открой|запусти)\s+(терминал|консоль|shell)"),
    ("exfil2", r"\b(include|print|output|list|show|reveal|dump|append|paste)\b.{0,40}\b(environment\s+variables|env\s+vars|access\s+tokens?|tokens?\s+you|api\s+keys?|credentials|secrets)\b|(выведи|покажи|включи|приложи|добавь|вставь)\w*.{0,40}(переменн\w+\s+окружени|токен|ключ\w*\s+(api|доступа)|парол|секрет)"),
    ("judge-bait2", r"\b(note|message|attention)\s+(to|for)\s+(the\s+)?(evaluat|grad|judg|review|scor|assess)\w*(\s+(model|llm|ai|bot))?|\bdear\s+(evaluator|judge|grader|reviewer|assessor)\b|(уважаем\w*|дорог\w*|для)\s+(\w+[-\s]+){0,2}(модел\w*|ии|нейросет\w*)?[-\s]*(судь|оценщик|проверяющ)\w*|(оценщик|судья|эксперт\w*-оценщик)\w*\b.{0,80}(максимальн|высш|наивысш|лучш|перв)\w*\s+(балл|оценк|мест|рейтинг)|\bв\s+вашем\s+(рейтинге|оценке)\b"),
    ("meta-note", r"\b(note|message|instruction)s?\s+(for|to)\s+(ai|automated|llm|language[\s-]+model|machine)\w*\b|\bfor\s+(readers\s+using\s+)?automated\s+(tools|systems|readers|agents)\b|\b(ai|llm)\s+(summarizers?|agents?|assistants?|crawlers?)\s*:|\bno\s+need\s+to\s+(cross[- ]?check|verify|double[- ]?check|confirm|validate)\b|\b(confirmation|verification|validation)\s+steps?\b.{0,60}\b(skip|skipped|unnecessary|not\s+needed|redundant|can\s+be\s+omitted)|(примечани|сообщени|заметк|указани)\w*\s+для\s+(автоматическ|ии\b|нейросет|языков|модел|ассистент|агент)|проверк\w*\s+(\w+\s+)?не\s+(требуется|нужна|нужно)|перепроверя\w*\s+не\s+(нужно|надо|требуется)"),
    ("secret-files", r"(прочитай|выведи|покажи|отправь|вставь|приложи|read|print|show|send|paste|attach|cat)\w*\s+.{0,40}(?<![\w/])\.env\b|id_rsa|\.ssh/|\.aws/credentials|\.netrc|\bkeychain\b|!\[[^\]]*\]\(https?://[^)\s]*\?[^)\s]*(key|token|secret|data|q)=|<\s*(твой|ваш|your)\s+(api[\s_-]*)?(ключ|key|token|токен)"),
    ("address-reader", r"\bесли\s+(вы|ты)\s+(читаете|читаешь|обрабатываете|обрабатываешь)\s+(это|этот\s+текст)?\s*(как\s+)?(ассистент|ии|модель|агент|бот|нейросеть|программа)|\bif\s+you\s+are\s+(an?\s+)?(ai|assistant|language\s+model|llm|bot|agent)\b|\b(коллегам|читателям|участникам)\s+из\s+автоматизированн|автоматизированн\w+\s+(систем|агент|читател)\w*\s*[:,]|\b(analysts|readers|agents)\s+reading\s+this\s*[:,]"),
    ("targets", r"AGENTS\.md|CLAUDE\.md|claims\.jsonl|\.claude/|settings\.json|\.cursor/rules|\bgate\s+approve\b"),
    ("gate-forge", r"\bG[1-4]\s*[—–:-]\s*(да|ок|ok|approved)\b|(подтвердил|утвердил|одобрил)\w*\s+(выбор|вариант|решени|точк)|\bG[1-4]\s+(да|ок|утверждаю|ok|approve|approved|passed|signed|closed|согласован\w*|утвержд\w*|пройден\w*)\b|(человек|руководитель|заказчик|директор)\s+(уже\s+)?(подтвердил|утвердил|одобрил|разрешил)|пользователь\s+уже\s+(подтвердил|утвердил|одобрил)|\b(user|human)\s+(has\s+)?(already\s+)?(approved|confirmed|authorized)"),
    ("reviewer-address", r"\b(llm|ai)[\s-]+(reviewer|judge|grader|evaluator)\b|\b(give|award|rate|score|grade)\b.{0,40}\b\d{1,3}\s*(/|out of)\s*\d{1,3}\b|(проверяющ\w+|ии-эксперт\w*|жюри)\s*[:,].{0,60}(поставь|оцени|присуди)"),
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
