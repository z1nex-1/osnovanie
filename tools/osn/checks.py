from __future__ import annotations

import re

from . import claims as claims_mod
from .store import Project, read_json

NUM = re.compile(r"(?<![\w.])\d[\d\s ]*(?:[.,]\d+)?\s*(?:%|₽|руб|млн|млрд|тыс|\$|USD)", re.I)
DEMAND = re.compile(r"спрос|распростран[её]нност|готов\w*\s+плат|доля\s+пользовател|сколько\s+(людей|клиентов)|востребованн", re.I)
SECTIONS = {
    "product.md": ["Аудитория", "Альтернативы", "Направление", "Отказы", "Экономика", "Возможности"],
    "communication.md": ["Аудитория", "Барьер", "Ключевое сообщение", "Подтверждение", "Каналы", "Обещания"],
    "validation.md": ["Ожидаемое изменение", "С чем сравниваем", "Срок", "Нежелательные последствия"],
}
WEASEL = re.compile(r"\b(значительн\w*|существенн\w*|растущ\w*|огромн\w*|революц\w*|уникальн\w*)\b", re.I)
STALE_MONTHS = 18
BRIEF_FIELDS = {"question": "вопрос", "goal": "цель", "recipient": "получатель", "constraints": "ограничения", "data": "доступные данные"}


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def section(text: str, title: str) -> str:
    """Текст раздела Markdown от заголовка с title до следующего заголовка того же или более высокого уровня."""
    match = re.search(rf"^(#+)\s[^\n]*{title}[^\n]*$", text, re.I | re.M)
    if not match:
        return ""
    level = len(match.group(1))
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
    return rest[: end.start()] if end else rest


def items(block: str) -> list[str]:
    return [line for line in block.splitlines() if re.match(r"\s*(?:[-*]|\d+[.)])\s+\S", line) or re.match(r"\|\s*[^-|\s]", line)]


def _intake(project: Project) -> list[str]:
    brief = read_json(project.state / "brief.json", {})
    return [f"в постановке нет поля «{label}»" for key, label in BRIEF_FIELDS.items() if not brief.get(key)]


def _plan(project: Project) -> list[str]:
    problems = []
    questions = project.current("questions")
    if len(questions) < 3:
        problems.append("нужно не меньше трёх вопросов, способных изменить решение")
    problems += [f"у вопроса {q['id']} не указано, что он меняет в решении" for q in questions.values() if not q.get("changes")]
    if len(project.records("source_plan")) < 3:
        problems.append("в плане источников меньше трёх позиций")
    if not project.meta().get("budget_usd"):
        problems.append("не задан бюджет: osnovanie budget set <usd>")
    return problems


def _research(project: Project) -> list[str]:
    problems = []
    sources = [s for s in project.current("sources").values() if s["status"] == "active"]
    claims = project.current("claims")
    if len(sources) < 3:
        problems.append("загружено меньше трёх источников")
    if len(claims) < 5:
        problems.append("записано меньше пяти утверждений")
    unverified = [c["id"] for c in claims.values() if c["type"] in claims_mod.NEEDS_SOURCE and c["status"] == "unverified"]
    if unverified:
        problems.append("не проверены цитаты: " + ", ".join(unverified[:8]) + ". Запустите osnovanie verify")
    contested = [c["id"] for c in claims.values() if c["status"] == "contested" and not c.get("superseded_by")]
    if contested:
        problems.append("спорные утверждения не разобраны (исправить или отозвать): " + ", ".join(contested[:8]))
    open_questions = [q["id"] for q in project.current("questions").values() if q["status"] == "open"]
    if open_questions:
        problems.append("открытые вопросы: " + ", ".join(open_questions) + ". Ответьте или запишите как пробел с причиной")
    if not (project.outputs / "situation.md").exists():
        problems.append("нет картины ситуации outputs/situation.md")
    else:
        text = (project.outputs / "situation.md").read_text("utf-8")
        for title in ("Что известно", "Чего мы не знаем"):
            if title.lower() not in text.lower():
                problems.append(f"в situation.md нет раздела «{title}»")
        gaps = [q for q in project.current("questions").values() if q["status"] == "gap"]
        unknown = items(section(text, "Чего мы не знаем"))
        if gaps and len(unknown) < len(gaps):
            problems.append(f"в «Чего мы не знаем» {len(unknown)} пунктов, а пробелов {len(gaps)}: каждый пробел назван с причиной")
    return problems


def _options(project: Project) -> list[str]:
    problems = []
    options = project.current("options")
    if len(options) < 3:
        problems.append("вариантов меньше трёх")
    kinds = {o["kind"] for o in options.values()}
    if "keep" not in kinds:
        problems.append("нет варианта «оставить как есть»")
    if "close" not in kinds:
        problems.append("нет варианта «закрыть направление»")
    claims = project.current("claims")
    for option in options.values():
        objections = option.get("objections", [])
        if not any(o.get("by") == "skeptic" for o in objections):
            problems.append(f"{option['id']}: нет возражения скептика")
        if not option.get("loses_if"):
            problems.append(f"{option['id']}: не указано, при каких условиях вариант проигрывает")
        if len(objections) < 2:
            problems.append(f"{option['id']}: меньше двух возражений")
        if option.get("kind") != "keep" and not option.get("basis"):
            problems.append(f"{option['id']}: вариант не опирается ни на одно утверждение")
        if not option.get("what_changes"):
            problems.append(f"{option['id']}: не указано, что изменит решение")
        missing = [c for c in option.get("basis", []) if c not in claims]
        if missing:
            problems.append(f"{option['id']}: нет утверждений {', '.join(missing)}")
    if not any(o.get("by") == "closure-advocate" for option in options.values() for o in option.get("objections", [])):
        problems.append("нет аргументов адвоката закрытия")
    titles = [set(re.findall(r"\w{4,}", (o.get("thesis") or "").lower())) for o in options.values()]
    for i, left in enumerate(titles):
        for right in titles[i + 1:]:
            if left and right and len(left & right) / len(left | right) > 0.7:
                problems.append("два варианта почти совпадают по описанию, нужны содержательно разные")
    if not project.records("model_runs"):
        problems.append("модель вариантов не посчитана: osnovanie model run")
    return problems


def doc_problems(project: Project, files: list[str] | None = None) -> list[str]:
    problems = []
    claims = project.current("claims")
    for name in files or list(SECTIONS):
        path = project.outputs / name
        if not path.exists():
            problems.append(f"нет файла outputs/{name}")
            continue
        text = path.read_text("utf-8")
        for title in SECTIONS.get(name, []):
            if not re.search(rf"^#+\s.*{title}", text, re.I | re.M):
                problems.append(f"{name}: нет раздела «{title}»")
        for paragraph in paragraphs(text):
            if paragraph.startswith("#") or paragraph.startswith("|"):
                continue
            refs = claims_mod.refs_in(paragraph)
            if NUM.search(paragraph) and not refs:
                problems.append(f"{name}: число без ссылки на утверждение: «{paragraph[:70]}…»")
            kinds = [claims_mod.effective_type(claims[r]) for r in refs if r in claims]
            if DEMAND.search(paragraph) and kinds and all(k == "synthetic" for k in kinds):
                problems.append(f"{name}: спрос обоснован только модельными респондентами: «{paragraph[:70]}…»")
        for ref in claims_mod.refs_in(text):
            if ref not in claims:
                problems.append(f"{name}: ссылка на несуществующее {ref}")
            elif claims_mod.effective_type(claims[ref]) in {"retracted", "stale"}:
                problems.append(f"{name}: ссылка на устаревшее или отозванное {ref}")
    return problems


TABLE_ROW = re.compile(r"^\|\s*(CAP\d+|P\d+)\s*\|(.*)\|\s*$", re.M)


def _rows(text: str) -> dict[str, list[str]]:
    return {m.group(1): [c.strip() for c in m.group(2).split("|")] for m in TABLE_ROW.finditer(text)}


def promise_problems(project: Project) -> list[str]:
    product, comms = project.outputs / "product.md", project.outputs / "communication.md"
    if not (product.exists() and comms.exists()):
        return []
    problems = []
    caps = {k: v for k, v in _rows(product.read_text("utf-8")).items() if k.startswith("CAP")}
    promises = {k: v for k, v in _rows(comms.read_text("utf-8")).items() if k.startswith("P")}
    claims = project.current("claims")
    if not caps:
        problems.append("product.md: таблица возможностей пуста (CAP1 | что | есть/план | [cNNN])")
    if not promises:
        problems.append("communication.md: таблица обещаний пуста (P1 | обещание | CAP1 | сейчас/после | [cNNN])")
    for pid, row in promises.items():
        if len(row) < 4:
            problems.append(f"{pid}: в строке обещания нужны текст, возможность, срок, доказательство")
            continue
        _, cap, when, proof = row[:4]
        if cap not in caps:
            problems.append(f"{pid}: обещание не связано ни с одной возможностью продукта ({cap or 'пусто'})")
            continue
        if when.lower().startswith("сейчас") and not caps[cap][1].lower().startswith("есть"):
            problems.append(f"{pid}: обещание «сейчас», а возможность {cap} только в плане")
        refs = claims_mod.refs_in(proof)
        kinds = [claims_mod.effective_type(claims[r]) for r in refs if r in claims]
        if not kinds or not any(k in {"fact", "calculation"} for k in kinds):
            problems.append(f"{pid}: обещание без проверенного доказательства")
    return problems


def structure_problems(project: Project) -> list[str]:
    problems = []
    out = project.outputs
    if (out / "product.md").exists():
        text = (out / "product.md").read_text("utf-8")
        if len(items(section(text, "Отказы"))) < 2:
            problems.append("product.md: в «Осознанных отказах» меньше двух пунктов")
        direction = section(text, "Направление")
        if direction and not re.search(r"диагноз", direction, re.I):
            problems.append("product.md: в «Направлении» нет диагноза (methods/rumelt.md)")
    if (out / "communication.md").exists():
        text = (out / "communication.md").read_text("utf-8")
        if len(section(text, "Барьер").strip()) < 80:
            problems.append("communication.md: барьер не раскрыт (methods/behavior-barrier.md)")
    if (out / "validation.md").exists():
        text = (out / "validation.md").read_text("utf-8")
        if not re.search(r"\d+\s*(дн|недел|месяц|day|week)", section(text, "Срок"), re.I):
            problems.append("validation.md: срок наблюдения без числа и единицы")
        if len(items(section(text, "Нежелательные последствия"))) < 2:
            problems.append("validation.md: меньше двух антиметрик в «Нежелательных последствиях»")
        if not re.search(r"контрол|до запуска|период до|разност|A/B|рандом", section(text, "С чем сравниваем"), re.I):
            problems.append("validation.md: не указано, с чем сравниваем (контроль, период до, квазиэксперимент)")
    if not (out / "monday.md").exists():
        problems.append("нет outputs/monday.md — страницы «что делать в понедельник»")
    return problems


def language_problems(project: Project) -> list[str]:
    found = []
    for name in ("situation.md", "product.md", "communication.md", "validation.md", "monday.md"):
        path = project.outputs / name
        if not path.exists():
            continue
        for paragraph in paragraphs(path.read_text("utf-8")):
            word = WEASEL.search(paragraph)
            if word and not NUM.search(paragraph):
                found.append(f"{name}: «{word.group(0)}» без числа")
    return found


def stale_sources(project: Project) -> list[str]:
    import datetime as dt

    today = dt.date.today()
    sources = project.current("sources")
    used = {c.get("source_id") for c in project.current("claims").values() if c["type"] == "fact" and c["status"] == "verified"}
    old = []
    for sid in sorted(s for s in used if s in sources):
        published = (sources[sid].get("published_at") or "")[:10]
        try:
            date = dt.date.fromisoformat({4: published + "-01-01", 7: published + "-01"}.get(len(published), published))
        except ValueError:
            continue
        if (today - date).days > STALE_MONTHS * 30:
            old.append(f"{sid} ({published})")
    return old


def _strategy(project: Project) -> list[str]:
    problems = doc_problems(project) + promise_problems(project) + structure_problems(project) + language_problems(project)
    shortlist = set(project.meta().get("shortlist", []))
    for name in ("product.md", "communication.md"):
        path = project.outputs / name
        if path.exists():
            found = re.search(r"^Вариант:\s*(O\d+)", path.read_text("utf-8"), re.M)
            if not found:
                problems.append(f"{name}: нет строки «Вариант: O…»")
            elif shortlist and found.group(1) not in shortlist:
                problems.append(f"{name}: вариант {found.group(1)} не прошёл точку G3")
    return problems


BY_STAGE = {"intake": _intake, "plan": _plan, "research": _research, "options": _options, "strategy": _strategy}


def failures(project: Project, stage: str) -> list[str]:
    return BY_STAGE.get(stage, lambda _: [])(project)


def full_report(project: Project) -> list[tuple[str, bool, str]]:
    from . import workflow

    stage = workflow.load(project)["stage"]
    order = workflow.STAGES
    report = []
    for name in order[: order.index(stage) + 1]:
        problems = failures(project, name)
        report.append((workflow.STAGE_LABEL[name], not problems, "; ".join(problems)))
    claims = project.current("claims")
    bad = [c["id"] for c in claims.values() if c["type"] == "fact" and c["status"] == "verified" and not c.get("verified_by")]
    report.append(("факт только после проверки", not bad, ", ".join(bad)))
    synthetic = [c["id"] for c in claims.values() if c["type"] == "synthetic" and c["status"] == "verified"]
    report.append(("синтетика не стала фактом", not synthetic, ", ".join(synthetic)))
    weasel = language_problems(project)
    report.append(("без пустых усилителей", not weasel, "; ".join(weasel[:6])))
    old = stale_sources(project)
    report.append((f"факты не старше {STALE_MONTHS} мес. или старость названа", True, ("предупреждение: " + ", ".join(old)) if old else ""))
    return report


def _claims_summary(project: Project) -> str:
    counts: dict[str, int] = {}
    for claim in project.current("claims").values():
        kind = claims_mod.effective_type(claim)
        counts[kind] = counts.get(kind, 0) + 1
    return ", ".join(f"{claims_mod.LABEL[k]}: {v}" for k, v in sorted(counts.items())) or "пока нет"


def gate_packet(project: Project, gate: str, summary: str | None) -> str:
    from . import cost, model, workflow

    meta = project.meta()
    lines = [f"# Точка {gate}: {workflow.GATE_LABEL[gate]}", ""]
    if summary:
        lines += [summary, ""]
    total = cost.summary(project)
    lines += [f"Потрачено: ${total['llm_usd']:.2f} из бюджета ${meta.get('budget_usd') or 0:.2f}. Утверждения: {_claims_summary(project)}.", ""]
    if gate == "G1":
        brief = read_json(project.state / "brief.json", {})
        lines += ["## Как агент понял задачу", ""] + [f"- {label}: {brief.get(key)}" for key, label in BRIEF_FIELDS.items()]
        lines += ["", "Ответьте в чате: `G1 да` или `G1 правка: что изменить`."]
    elif gate == "G2":
        lines += ["## Вопросы, которые могут изменить решение", ""]
        lines += [f"- {q['id']}: {q['text']} → меняет: {q['changes']}" for q in project.current("questions").values()]
        lines += ["", "## Источники", ""] + [f"- {r['url']} — {r['why']}" for r in project.records("source_plan")]
        lines += ["", "Ответьте: `G2 да` (можно `G2 да бюджет 8`) или `G2 правка: ...`."]
    elif gate == "G3":
        lines += ["## Варианты", ""]
        run = model.last_run(project)
        for option in project.current("options").values():
            res = (run or {}).get("results", {}).get(option["id"], {})
            head = f"**{option['id']}. {option['title']}**"
            if res:
                head += f" — база {res['base']:,.0f} {run['output_unit']}, диапазон {res['p10']:,.0f}…{res['p90']:,.0f}, лидирует в {res['p_best'] * 100:.0f}% случаев"
            lines += [head, option.get("thesis", ""), "Возражения:"] + [f"- {o['by']}: {o['text']}" for o in option.get("objections", [])]
            lines += [f"Проигрывает, если: {option.get('loses_if')}", ""]
        lines += ["Ответьте: `G3 да O2 O3` (какие варианты развивать в стратегии) или `G3 правка: ...`."]
    elif gate == "G4":
        lines += ["Готовы: outputs/product.md, outputs/communication.md, outputs/validation.md, outputs/trust-map.html.", ""]
        lines += ["Ответьте: `G4 да O2 ответственный Имя` или `G4 правка: ...`."]
    elif gate == "R":
        lines += ["Ответьте: `R да` (принять пересмотр) или `R правка: ...`."]
    return "\n".join(lines) + "\n"
