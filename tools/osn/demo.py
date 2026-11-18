"""Эталонный проект «МегаНейросети»: собирается настоящими функциями движка из demo/meganeuroseti-sources.md.

Источники и цитаты берутся из файла, формулировки допущений, вариантов и стратегии авторские и помечены в project.json.
Точки человека закрываются каналом demo-seed: score и сайт считают такое закрытие не человеческим.
"""
from __future__ import annotations

import re
import shutil

from . import claims as claims_mod, cost, data_tools, model, review, sources, trustmap, verify, workflow
from .store import OsnError, Project, active_file, atomic_write, now, repo_root

SLUG = "meganeuroseti-ref"
CHANNEL = "demo-seed"
SOURCE_FILE = "demo/meganeuroseti-sources.md"
CAPTURED = "2026-11-14T12:00:00+03:00"
NOTE = "Эталонный проект: собран командой demo seed из 27 проверенных цитат. Формулировки вариантов и допущений авторские, это не прогон агента и не ответы руководителя."
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
EVENT_ROWS = {13, 14}
KIND_BY_GROUP = {"1": "primary", "2": "secondary", "3": "secondary", "4": "secondary"}
PRIMARY_HOSTS = ("megafon.ru", "gogpt.ru", "chadgpt.ru", "bothub.chat", "wciom.ru")


def parse_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) != 7 or not cells[0].isdigit():
            continue
        quote = cells[5]
        start, end = quote.find("«"), quote.rfind("»")
        if start < 0 or end <= start:
            raise OsnError(f"В строке {cells[0]} нет цитаты в «ёлочках».")
        date = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", cells[4])
        published = f"{date.group(3)}-{MONTHS[date.group(2)]:02d}-{int(date.group(1)):02d}" if date and date.group(2) in MONTHS else None
        rows.append({"n": int(cells[0]), "url": cells[2], "title": cells[3], "published": published, "quote": quote[start + 1:end].strip()})
    return rows


# Формулировки фактов: числа берутся только из цитаты, иначе verify откажет.
FACTS = {
    1: ("МегаФон запустил МегаНейросети для корпоративных абонентов, в версии «Лайт» есть бесплатные генерации", "segment", 270, None, None),
    2: ("Тариф «Про»: 10 моделей, до 50 запросов в сутки, 299 ₽ в месяц", "price", 90, 299, "руб/чел/мес"),
    3: ("6 токенов стоят 1 ₽", "price", 90, 6, "токен/руб"),
    4: ("МегаФон адресует продукт и фрилансерам с самозанятыми, а не только корпоративным клиентам", "segment", 270, None, None),
    5: ("Пресса: бесплатная подписка даёт шесть зарубежных моделей, платная — 25", "offer", 270, None, None),
    6: ("«Лайт»: бесплатно, до 10 запросов в сутки в 6 нейросетях; «Про»: 299 рублей в месяц и до 50 запросов в сутки", "price", 90, None, None),
    7: ("Директор по продуктам корпоративного бизнеса МегаФона обещает расширять список моделей в ближайшие месяцы", "roadmap", 270, None, None),
    8: ("Предприниматели сталкиваются с трудностями при оплате зарубежных сервисов и настройкой VPN", "pain", 270, None, None),
    9: ("Сгенерированный контент и диалог сохраняются 30 дней", "legal", 270, None, None),
    10: ("МТС продаёт тариф «РИИЛ» с ИИ за 890 рублей в месяц", "price", 90, 890, "руб/чел/мес"),
    11: ("Пакеты МегаФона в Telegram: 1000 запросов GPT за 199 рублей, 200 запросов к Claude 4.5 за 599 рублей", "price", 90, None, None),
    12: ("Абонент тарифа МегаФона «Персональный» получает 20 бесплатных запросов в неделю", "offer", 90, None, None),
    13: ("Билайн начисляет клиентам обновлённой подписки Bee 300 токенов в месяц без дополнительной платы", "price", 90, 300, "токен/мес"),
    14: ("Обращения к ИИ-сервисам у клиентов Билайна за год выросли более чем в три раза (данные самого Билайна)", "demand", 270, None, None),
    15: ("«Алиса Плюс» подорожала со 100 ₽ до 199 ₽ в месяц", "price", 90, 199, "руб/чел/мес"),
    16: ("Сбер оставит GigaChat бесплатным, пока растёт аудитория, а за часть функций возьмёт деньги позже", "competition", 270, None, None),
    17: ("GoGPT берёт от 699 руб в месяц", "price", 90, 699, "руб/чел/мес"),
    18: ("Chad AI: тариф для команд стоит 1 690 ₽ в месяц", "price", 90, 1690, "руб/чел/мес"),
    19: ("BotHub: тариф Deluxe стоит 16 $ за 10 000 000 Caps", "price", 90, None, None),
    20: ("78% интернет-пользователей России обращались к нейросетям за последний год (опрос ВЦИОМ)", "market", 240, 78, "%"),
    21: ("Для пользователей нейросеть прежде всего рабочий инструмент", "market", 240, None, None),
    22: ("Платные подписки на сервисы называют барьером 15 процентов опрошенных", "market", 240, 15, "%"),
    23: ("Месячный охват ИИ-сервисов в России в сентябре достиг 55% населения старше 12 лет", "market", 240, 55, "%"),
    24: ("DeepSeek пользуются 13 млн человек (11%), GigaChat — 6 млн (5%)", "competition", 240, None, None),
    25: ("44% россиян используют модели ИИ против 39,2% годом ранее", "market", 240, 44, "%"),
    26: ("Доля россиян, пользующихся нейросетями не реже раза в неделю, за полгода выросла с 51 до 58% (ВЦИОМ в пересказе AdIndex)", "market", 240, None, None),
    27: ("По трафику МегаФона аудитория ИИ-сервисов в России выросла на 110%", "demand", 240, 110, "%"),
}

QUESTIONS = [
    ("Кому продавать: корпоративным клиентам, малому бизнесу или частным абонентам?", "выбор между «как есть», сдвигом в малый бизнес и тарифом", ["r1", "r4", "r11", "X2"]),
    ("Выдержит ли цена 299 ₽ давление бесплатных и более дешёвых предложений?", "цена пакета и роль «Лайт»", ["r2", "r15", "r17", "r16", "K1"]),
    ("Достаточно ли велика аудитория, которая платит за нейросети?", "закрывать направление или нет", ["r20", "r23", "r25", "X3"]),
]
GAPS = [
    ("Сколько абонентов платит за «Про» и какова конверсия «Лайт» → «Про»?", "число подписчиков, активаций и конверсия нигде не раскрыты", "выбор между вариантами"),
    ("Какова маржа направления и закупочная цена токенов?", "модели оплачиваются через посредника, выручка и расходы направления не раскрыты", "закрывать или оставлять"),
    ("Как ведут себя удержание и отток платящих?", "данных об удержании и среднем чеке в открытых источниках нет", "цена и пакет"),
    ("Совместимо ли хранение данных 30 дней и 152-ФЗ с B2B-продажами?", "условия хранения, локализации и соответствие 152-ФЗ не раскрыты", "выход на крупный B2B"),
]


def _quiet(project: Project, gate: str, text: str) -> None:
    reply = workflow.handle_human_prompt(project, text, CHANNEL)
    state = workflow.load(project)
    if state["gates"].get(gate, {}).get("status") != "approved":
        raise OsnError(f"Точка {gate} не закрылась: {reply}")


def _supersede(project: Project, old: str, data: dict) -> dict:
    new = claims_mod.supersede(project, old, data)
    return claims_mod.update(project, new["id"], author=CHANNEL)


def _question(project: Project, text: str, changes: str, **extra) -> dict:
    record = {"id": project.next_id("questions", "q"), "text": text, "changes": changes, "status": "open", "ts": now(), **extra}
    project.append("questions", record)
    return record


def _option(project: Project, data: dict, objections: list[tuple[str, str, list[str]]]) -> None:
    record = {"objections": [], "ts": now(), **data}
    project.append("options", record)
    for by, text, refs in objections:
        record = {**project.current("options")[data["id"]]}
        record["objections"] = record["objections"] + [{"by": by, "text": text, "claims": refs, "ts": now()}]
        project.append("options", record)
    project.event("option", id=data["id"])


class Builder:
    def __init__(self, project: Project):
        self.project = project
        self.ids: dict[str, str] = {}
        self.lenient = False

    def claim(self, key: str, data: dict, extra: dict | None = None) -> str:
        record = claims_mod.add(self.project, self.resolve(data), author=CHANNEL)
        if extra:
            claims_mod.update(self.project, record["id"], **extra)
        self.ids[key] = record["id"]
        return record["id"]

    def resolve(self, data: dict) -> dict:
        deps = [self.ids[k] for k in data.get("depends_on", [])]
        return {**data, "depends_on": deps}

    def ref(self, *keys: str) -> str:
        if self.lenient:
            return "[" + ", ".join(self.ids.get(k, "c000") for k in keys) + "]"
        return "[" + ", ".join(self.ids[k] for k in keys) + "]"


def _model_def(b: Builder) -> dict:
    ids = b.ids
    return {
        "params": {
            "users": {"label": "подключили «Лайт»", "unit": "чел", "low": 5000, "base": 20000, "high": 60000, "claim": ids["A1"]},
            "conv": {"label": "доля перешедших в «Про»", "unit": "1", "low": 0.02, "base": 0.05, "high": 0.10, "claim": ids["A2"]},
            "price": {"label": "цена «Про»", "unit": "руб/чел/мес", "low": 199, "base": 299, "high": 399, "claim": ids["r2"]},
            "tokens": {"label": "токены сверх подписки", "unit": "руб/чел/мес", "low": 30, "base": 120, "high": 250, "claim": ids["A3"]},
            "unitcost": {"label": "закупка моделей на пользователя «Про»", "unit": "руб/чел/мес", "low": 40, "base": 90, "high": 180, "claim": ids["A4"]},
            "smb": {"label": "рост платящих при продаже малому бизнесу", "unit": "1", "low": 1.0, "base": 1.6, "high": 2.5, "claim": ids["A5"]},
            "bundle_price": {"label": "доплата за ИИ в тарифе", "unit": "руб/чел/мес", "low": 49, "base": 99, "high": 149, "claim": ids["A6"]},
            "bundle_users": {"label": "абоненты тарифа с ИИ", "unit": "чел", "low": 2000, "base": 8000, "high": 25000, "claim": ids["A7"]},
            "bundle_cost": {"label": "закупка моделей на абонента тарифа", "unit": "руб/чел/мес", "low": 10, "base": 25, "high": 60, "claim": ids["A8"]},
        },
        "constants": {
            "fixed": {"value": 450000, "unit": "руб/мес"},
            "smb_sales": {"value": 150000, "unit": "руб/мес"},
            "cannibal": {"value": 90000, "unit": "руб/мес"},
            "winddown": {"value": 200000, "unit": "руб/мес"},
        },
        "options": {
            "O1": "users * conv * (price + tokens - unitcost) - fixed",
            "O2": "users * conv * smb * (price + tokens - unitcost) - fixed - smb_sales",
            "O3": "users * conv * (price + tokens - unitcost) + bundle_users * (bundle_price - bundle_cost) - fixed - cannibal",
            "O4": "-winddown",
        },
        "output_unit": "руб/мес",
    }


def _k(value: float) -> str:
    return f"{value / 1000:,.0f} тыс. ₽".replace(",", " ")


def _model_text(run: dict) -> str:
    parts = [f"{o}: {_k(r['base'])} (P10 {_k(r['p10'])}, P90 {_k(r['p90'])}, лидер в {r['p_best'] * 100:.0f}% прогонов)" for o, r in run["results"].items()]
    return "Прибыль в месяц по модели вариантов, база: " + "; ".join(parts) + f". Лидер: {run['ranking'][0]}"


def _docs(b: Builder, phase: str) -> dict[str, str]:
    r = b.ref
    revised = phase == "revised"
    bundle = "59 ₽" if revised else "99 ₽"
    beeline = f"Билайн уже начисляет 300 токенов в месяц без доплаты {r('F13')}. " if revised else ""
    situation = f"""# Картина ситуации

## Что известно

МегаНейросети запущены как корпоративная услуга: «Лайт» бесплатно, «Про» за 299 ₽ в месяц и до 50 запросов в сутки {r('r1', 'r2')}.

Токены продаются по курсу 6 токенов за 1 ₽ {r('r3')}.

Платные предложения конкурентов стоят от 199 ₽ до 1 690 ₽ в месяц, медиана 794,5 ₽ {r('K1', 'r15', 'r17', 'r18')}.

{('Билайн включает 300 токенов в месяц в подписку без доплаты ' + r('F13') + '.') if revised else 'Операторы пока продают ИИ отдельной платной услугой ' + r('A0') + '.'}

Охват ИИ-сервисов в России: 55% населения старше 12 лет в сентябре {r('r23')}; 78% интернет-пользователей обращались к нейросетям за год {r('r20')}. Базы разные, напрямую цифры не сравниваются {r('X3')}.

Платные подписки называют барьером 15 процентов опрошенных {r('r22')}; главная боль клиента — оплата зарубежных сервисов и VPN {r('r8')}.

Источники расходятся: платная подписка то 25 моделей, то 10 в «Про» {r('X1')}; клиент то корпоративный, то любой {r('X2')}.

## Чего мы не знаем

- Сколько абонентов платит за «Про» и какова конверсия «Лайт» → «Про»: нигде не раскрыто, в модели стоит допущение {r('A1', 'A2')}.
- Маржа направления и закупочная цена токенов: данных нет, в модели допущение {r('A4')}.
- Удержание и отток платящих: данных об удержании нет.
- Совместимость хранения данных 30 дней с 152-ФЗ в B2B {r('r9')}: условия не раскрыты.
- Спрос малого бизнеса: опросов этого сегмента нет, остаётся гипотеза {r('H2')}.
- Готовность абонентов доплатить за ИИ в тарифе: допущение {r('A6')}, проверяется пилотом.
"""
    product = f"""# Продуктовая стратегия

Вариант: O3

## Аудитория

Первый слой: абоненты тарифов, где ИИ идёт частью тарифа связи, и малый бизнес на корпоративных тарифах. Корпоративная услуга остаётся как есть {r('r1')}.

Главная боль клиента — оплата зарубежных сервисов и VPN {r('r8')}; платные подписки как барьер назвали 15 процентов опрошенных {r('r22')}.

## Альтернативы

Бесплатные Алиса и GigaChat остаются главным конкурентом по цене {r('r16', 'r24')}. Независимые агрегаторы стартуют от 699 руб в месяц {r('r17')}, МТС продаёт тариф с ИИ за 890 рублей в месяц {r('r10')}.

{beeline}Вывод по ценам: 299 ₽ в месяц ниже медианы конкурентов 794,5 ₽, ценовой запас у «Про» есть {r('V1')}.

## Направление

Диагноз: продукт продаёт доступ к чужим моделям по подписке, а массовая аудитория уходит к бесплатным Алисе и GigaChat {r('r24')}. Платить будут те, кому нужны оплата без зарубежной карты и один счёт {r('r8')}. Путь «Лайт» → «Про» держится на конверсии, которую никто не раскрыл {r('V4')}.

Направление: не продавать ИИ отдельной подпиской массовому клиенту, а встроить его в тариф связи как платный пакет {r('V3')}.

## Отказы

- не расширяем платный слой «Про» на массовую аудиторию: бесплатные Алиса и GigaChat забирают её {r('r24')}
- не закрываем направление сейчас: охват ИИ-сервисов 55% населения и удвоился за год {r('r23')}
- не масштабируем сдвиг в малый бизнес до проверки: опросов сегмента нет {r('H2')}

## Экономика

{('Расчёт после пересмотра: ' if revised else 'Расчёт: ') + _model_text(model.last_run(b.project) or {'results': {}, 'ranking': ['?']}) + ' ' + r('K2')}.

Допущения под расчётом названы допущениями: подключили «Лайт» {r('A1')}, конверсия {r('A2')}, доплата за ИИ в тарифе {bundle} {r('A6')}.

## Возможности

| CAP1 | токены за рубли по курсу 6 за 1 ₽ | есть | {r('r3')} |
| CAP2 | 10 моделей и до 50 запросов в сутки в «Про» | есть | {r('r2')} |
| CAP3 | ИИ внутри тарифа связи | план | {r('r10')} |
"""
    communication = f"""# Коммуникационная стратегия

Вариант: O3

## Аудитория

Абоненты, которые уже платят за связь и пользуются нейросетями: рабочий инструмент для большинства {r('r21')}.

## Барьер

Целевое поведение: подключить пакет ИИ в тарифе. Главный барьер: клиент уже имеет бесплатные Алису и GigaChat {r('r24')} и не видит, зачем платить, пока не упрётся в оплату зарубежных сервисов или VPN {r('r8')}.

## Ключевое сообщение

ИИ без зарубежной карты и VPN, с оплатой в одном счёте за связь {r('r8')}.

## Подтверждение

Токены продаются за рубли по курсу 6 токенов за 1 ₽ {r('r3')}; «Про» открывает 10 моделей {r('r2')}.

## Каналы

Личный кабинет МегаФона и страница тарифа. Рассылка только абонентам, которые уже пользовались «Лайт».

## Обещания

| P1 | Токены оплачиваются в рублях | CAP1 | сейчас | {r('r3')} |
| P2 | В «Про» доступны 10 моделей | CAP2 | сейчас | {r('r2')} |
| P3 | ИИ входит в тариф связи | CAP3 | после пилота | {r('r10')} |
"""
    validation = f"""# План проверки

Вариант: O3

## Ожидаемое изменение

Пилот пакета «ИИ в тарифе» на 5 000 абонентов: ожидаем, что 8% подключат платный пакет за 28 дней {r('A6', 'A7')}. Это допущение, пилот его проверяет.

## С чем сравниваем

Контрольная группа: такие же абоненты без предложения. Сравниваем подключение и отток за тот же период {r('A7')}.

## Срок

28 дней наблюдения и 60 дней на отток {r('A6')}.

## Пороги остановки

- подключение ниже 4% за 28 дней: останавливаем пилот и возвращаемся к варианту O1 {r('A6')}
- доплата ниже 59 ₽ в месяц не покрывает закупку моделей {r('A8')}

## Нежелательные последствия

- платные подписчики «Про» (299 ₽ в месяц) уходят в более дешёвый пакет в тарифе {r('r2')}
- рост жалоб на цену тарифа связи после добавления пакета {r('r10')}
"""
    monday = f"""# Что делать в понедельник

- Запросить у владельца продукта число подписчиков «Про» и конверсию из «Лайт»: от них зависят допущения {r('A1', 'A2')}.
- Собрать 10 интервью с закупщиками малого бизнеса: проверить гипотезу о сегменте {r('H2')}.
- Подготовить пилот пакета «ИИ в тарифе» на 5 000 абонентов {r('A7')}.
"""
    strategy = f"""# Стратегия: МегаНейросети

Рекомендуем вариант O3: встроить ИИ в тариф связи как платный пакет и проверить пилотом. Решение держится на допущениях, не на фактах {r('K2', 'V3')}.

{beeline}Факты рынка: охват ИИ-сервисов 55% населения старше 12 лет {r('r23')}, бесплатные Алиса и GigaChat держат массовую аудиторию {r('r24')}.

Цена «Про» 299 ₽ в месяц ниже медианы конкурентов 794,5 ₽ {r('V1')}. Доплата за ИИ в тарифе в расчёте: {bundle} в месяц {r('A6')}.

Что мы не знаем: число платящих и конверсию {r('A1', 'A2')}, маржу {r('A4')}. Пока их нет, решение остаётся ставкой {r('V4')}.

План: пилот на 5 000 абонентов 28 дней, остановка при подключении ниже 4% {r('A6')}.
"""
    return {"situation.md": situation, "product.md": product, "communication.md": communication, "validation.md": validation, "monday.md": monday, "strategy.md": strategy}


def _write_docs(b: Builder, phase: str) -> None:
    for name, text in _docs(b, phase).items():
        atomic_write(b.project.outputs / name, text)


def seed(force: bool = False) -> dict:
    root = repo_root()
    source_md = root / SOURCE_FILE
    if not source_md.exists():
        raise OsnError(f"Нет файла {SOURCE_FILE}.")
    project = Project(SLUG, root)
    if project.exists():
        if not force:
            raise OsnError(f"Проект {SLUG} уже есть. Пересобрать: osnovanie demo seed --force")
        shutil.rmtree(project.base)
    rows = parse_rows(source_md.read_text("utf-8"))
    if len(rows) != 27:
        raise OsnError(f"В файле источников {len(rows)} цитат вместо 27.")
    for folder in (project.raw, project.parsed, project.quarantine, project.state, project.outputs):
        folder.mkdir(parents=True, exist_ok=True)
    question = "Кому и как продавать МегаНейросети и продолжать ли направление?"
    project.save_meta({"slug": SLUG, "title": "МегаНейросети: эталонный проект", "question": question, "created_at": now(), "budget_usd": 1, "reference": True, "note": NOTE})
    if not active_file(root).exists():
        atomic_write(active_file(root), SLUG)
    project.event("created", question=question)
    b = Builder(project)

    brief = {"question": question, "goal": "выбрать между сохранением, сдвигом в малый бизнес, продажей с тарифом и закрытием", "recipient": "руководитель направления МегаНейросети (роль в демонстрации)", "constraints": "только открытые данные на 14.11.2026, внутренних цифр МегаФона нет", "data": "27 цитат из 18 открытых источников"}
    from .store import write_json

    write_json(project.state / "brief.json", brief)
    project.event("brief")
    workflow.open_gate(project, "G1", None)
    _quiet(project, "G1", "G1 да постановка собрана демо-командой")
    workflow.advance(project)

    by_url: dict[str, list[dict]] = {}
    for row in rows:
        by_url.setdefault(row["url"], []).append(row)
    q_ids = [_question(project, text, changes)["id"] for text, changes, _ in QUESTIONS]
    gap_ids = [_question(project, text, changes)["id"] for text, _, changes in GAPS]
    for url, group in by_url.items():
        sources.plan(project, url, group[0]["title"], "primary" if any(h in url for h in PRIMARY_HOSTS) else "secondary")
    workflow.open_gate(project, "G2", None)
    _quiet(project, "G2", "G2 да источники и бюджет собраны демо-командой")
    workflow.advance(project)

    source_ids: dict[str, str] = {}
    for url, group in by_url.items():
        initial = [r for r in group if r["n"] not in EVENT_ROWS]
        if not initial:
            continue
        source_ids[url] = _ingest(project, url, group)
    # Источники Билайна появятся только в событии пересмотра.
    for row in rows:
        if row["n"] in EVENT_ROWS:
            continue
        text, topic, expiry, value, unit = FACTS[row["n"]]
        kind = "opinion" if row["n"] == 7 else "fact"
        b.claim(f"r{row['n']}", {"text": text, "type": kind, "source_id": source_ids[row["url"]], "quote": row["quote"], "value": value, "unit": unit, "topic": topic, "expires": _expires(expiry)})
    verified = verify.run(project)
    if verified["contested"]:
        raise OsnError(f"Цитаты не прошли проверку: {verified['contested']}")

    table = "service,class,price\nАлиса Плюс,конкурент,199\nGoGPT,конкурент,699\nChad AI,конкурент,1690\nМТС РИИЛ,конкурент,890\nМегаФон Про,МегаФон,299\n"
    own = sources.ingest_bytes(project, table.encode("utf-8"), ".csv", "Таблица цен из цитат (собрана демо-командой)", "own", "derived:price-table", None)
    deps = [b.ids[k] for k in ("r15", "r17", "r18", "r10")]
    result = data_tools.calc(project, {"id": "price-median", "source": own["id"], "op": "median", "column": "price", "where": {"class": "конкурент"}, "text": "Медиана цен платных предложений конкурентов, ₽ в месяц", "unit": "руб/чел/мес", "depends_on": deps})
    b.ids["K1"] = result["claim"]
    claims_mod.update(project, result["claim"], author=CHANNEL)

    b.claim("A0", {"text": "Допущение: операторы-конкуренты не включают ИИ в базовый пакет бесплатно, ИИ продаётся отдельной услугой", "type": "assumption", "confidence": 0.6, "depends_on": ["r10"]}, {"verify_plan": "проверить тарифы Билайна, МТС, Т2 на их сайтах: страницы не открывались напрямую"})
    assumptions = [
        ("A1", "Допущение: к «Лайт» подключилось 20 000 абонентов (диапазон 5 000–60 000); цифра не раскрыта", 20000, "чел", [5000, 60000], 0.3, ["r1", "r6"], "запросить у владельца продукта число активаций «Лайт»"),
        ("A2", "Допущение: в «Про» переходит 5% подключивших «Лайт» (диапазон 2–10%)", 0.05, "1", [0.02, 0.10], 0.3, ["r2", "r6"], "запросить конверсию «Лайт» → «Про» или измерить в пилоте"),
        ("A3", "Допущение: активный пользователь «Про» докупает токены на 120 ₽ в месяц (диапазон 30–250 ₽)", 120, "руб/чел/мес", [30, 250], 0.35, ["r3"], "взять средний расход токенов из биллинга"),
        ("A4", "Допущение: закупка моделей стоит 90 ₽ на пользователя «Про» в месяц (диапазон 40–180 ₽); закупочные цены не раскрыты", 90, "руб/чел/мес", [40, 180], 0.3, ["r2"], "запросить закупочные цены у посредника"),
        ("A5", "Допущение: адресная продажа малому бизнесу увеличивает число платящих в 1,6 раза (диапазон 1,0–2,5)", 1.6, "1", [1.0, 2.5], 0.25, ["r1", "r4"], "пилот на 10 малых компаниях и интервью"),
    ]
    for key, text, value, unit, span, conf, dep, plan in assumptions:
        b.claim(key, {"text": text, "type": "assumption", "value": value, "unit": unit, "confidence": conf, "depends_on": dep}, {"range": span, "verify_plan": plan})
    b.claim("A6", {"text": "Допущение: абоненты соглашаются доплатить за ИИ в тарифе 99 ₽ в месяц (диапазон 49–149 ₽)", "type": "assumption", "value": 99, "unit": "руб/чел/мес", "confidence": 0.35, "depends_on": ["A0", "r10", "r12"]}, {"range": [49, 149], "verify_plan": "пилот пакета на 5 000 абонентов, 28 дней, контрольная группа"})
    b.claim("A7", {"text": "Допущение: пакет с ИИ в тарифе подключат 8 000 абонентов (диапазон 2 000–25 000)", "type": "assumption", "value": 8000, "unit": "чел", "confidence": 0.3, "depends_on": ["r10", "r11"]}, {"range": [2000, 25000], "verify_plan": "пилот пакета на 5 000 абонентов, 28 дней"})
    b.claim("A8", {"text": "Допущение: закупка моделей для абонента тарифа стоит 25 ₽ в месяц (диапазон 10–60 ₽): лёгкое использование", "type": "assumption", "value": 25, "unit": "руб/чел/мес", "confidence": 0.3, "depends_on": ["r3"]}, {"range": [10, 60], "verify_plan": "замерить расход токенов абонентов тарифа в пилоте"})

    hyps = [
        ("H1", "Корпоративные покупатели заплатят за агрегатор, если он снимает проблему оплаты и VPN", 0.55, ["r8", "r22"], "10 интервью с закупщиками и пилот с оплатой по счёту за 28 дней"),
        ("H2", "Малый бизнес и фрилансеры переходят из «Лайт» в «Про» чаще, чем крупные компании", 0.45, ["r4", "r18"], "10 интервью с закупщиками малого бизнеса и сравнение конверсии по сегментам"),
        ("H3", "Платный агрегатор выдержит бесплатных Алису и GigaChat только нишевой пользой: оплата, VPN, выбор моделей", 0.5, ["r16", "r24", "A0"], "сравнить причины подключения в пилоте и в опросе абонентов"),
        ("H4", "Связка с тарифом связи даёт больше подключений, чем разовая подписка", 0.4, ["r10", "r12"], "пилот пакета в тарифе против подписки «Про» на сопоставимых группах"),
    ]
    for key, text, conf, dep, plan in hyps:
        b.claim(key, {"text": text, "type": "hypothesis", "confidence": conf, "depends_on": dep}, {"verify_plan": plan})
    b.claim("F1", {"text": "Прогноз: цены массовых ИИ-подписок в России будут расти и дальше", "type": "forecast", "confidence": 0.5, "depends_on": ["r15"]}, {"verify_plan": "сверить цены «Алисы Плюс», GoGPT, Chad AI в мае 2027"})

    conflicts = [
        ("X1", "Расхождение источников: пресса и релиз говорят о 25 моделях в платной подписке, а страница продукта называет 10 моделей в «Про»", ["r5", "r2"], "не снято: нужна сверка со страницей продукта вручную"),
        ("X2", "Расхождение источников: релиз адресует услугу корпоративным абонентам, страница продукта называет и фрилансеров с самозанятыми, а МегаФон уже продаёт частным клиентам пакеты в Telegram", ["r1", "r4", "r11"], "не снято: целевой сегмент заявлен как B2B, продаётся шире"),
        ("X3", "Расхождение баз: 78% (интернет-пользователи, ВЦИОМ), 55% (население старше 12 лет, Mediascope) и 44% (россияне, ВШЭ) нельзя сравнивать напрямую", ["r20", "r23", "r25"], "снято: показываем каждую цифру со своей базой"),
        ("X4", "Расхождение формулировок: AdIndex пишет «58% россиян», а у ВЦИОМ 78% интернет-пользователей обращались к нейросетям за год", ["r26", "r20"], "снято: берём формулировку первоисточника"),
    ]
    for key, text, dep, resolution in conflicts:
        b.claim(key, {"text": text, "type": "hypothesis", "confidence": 0.9, "depends_on": dep, "topic": "conflict"}, {"conflict_between": [b.ids[k] for k in dep], "resolution": resolution, "verify_plan": "сверить источники вручную"})

    b.claim("V1", {"text": "Вывод: цена «Про» 299 ₽ ниже медианы конкурентов 794,5 ₽, ценовой запас есть, но конкуренты продают разные пакеты", "type": "hypothesis", "confidence": 0.7, "depends_on": ["r2", "K1"], "topic": "conclusion"}, {"verify_plan": "сравнить сопоставимые пакеты по числу запросов и моделей"})
    b.claim("V4", {"text": "Вывод: без числа платящих и конверсии решение остаётся ставкой на допущениях", "type": "hypothesis", "confidence": 0.85, "depends_on": ["A1", "A2"], "topic": "conclusion"}, {"verify_plan": "запросить данные у владельца продукта"})

    for number, (text, changes, _) in enumerate(QUESTIONS):
        refs = QUESTIONS[number][2]
        project.append("questions", {**project.current("questions")[q_ids[number]], "status": "answered", "claims": [b.ids[k] for k in refs], "ts": now()})
    for number, (text, why, _) in enumerate(GAPS):
        project.append("questions", {**project.current("questions")[gap_ids[number]], "status": "gap", "why": why, "ts": now()})

    _write_docs_for_research(b)
    workflow.advance(project)

    specs = [
        ({"id": "O1", "title": "Оставить как есть", "kind": "keep", "thesis": "Оставить корпоративный запуск без изменений: «Лайт» бесплатно, «Про» за 299 ₽, токены с баланса", "basis": ["r1", "r2"], "loses_if": "платящих меньше 1 000 человек: расходы на команду не покрываются", "what_changes": "если конверсия «Лайт» → «Про» окажется выше 7% или бесплатные пакеты конкурентов исчезнут", "portfolio": "не задевает другие продукты МегаФона"},
         [("skeptic", "Число платящих не раскрыто, окупаемость нельзя показать ни одной цифрой", ["V4"]), ("closure-advocate", "Бесплатные Алиса и GigaChat держат массовую аудиторию, платный агрегатор остаётся нишей", ["r24"])]),
        ({"id": "O2", "title": "Сместиться в малый бизнес", "kind": "change", "thesis": "Продавать малому бизнесу и фрилансерам с оплатой по счёту, пакетами по числу сотрудников и персональным менеджером", "basis": ["H2", "r4", "r8", "r18"], "loses_if": "малый бизнес не платит больше крупных клиентов: гипотеза H2 не подтверждается", "what_changes": "интервью с закупщиками малого бизнеса и сравнение конверсии по сегментам", "portfolio": "пересекается с корпоративными тарифами связи и пакетами в Telegram: возможна каннибализация"},
         [("skeptic", "Опросов малого бизнеса в открытых источниках нет, сегмент назван гипотезой", ["H2"]), ("pre-mortem", "Независимые агрегаторы уже продают командам по счёту и дешевле в пересчёте на пользователя", ["r18"])]),
        ({"id": "O3", "title": "Продавать вместе с тарифом связи", "kind": "change", "thesis": "Встроить ИИ в тариф связи как платный пакет рядом с услугой, а не продавать отдельной подпиской", "basis": ["H4", "r10", "r12", "r11"], "loses_if": "доплата за ИИ в тарифе ниже закупки моделей или абоненты не подключают пакет", "what_changes": "пилот пакета на 5 000 абонентов с контрольной группой", "portfolio": "каннибализирует тариф «Персональный» и пакеты в Telegram; общие расходы на закупку моделей"},
         [("skeptic", "Тариф «Персональный» уже даёт 20 бесплатных запросов в неделю: пакет придётся отличать от него", ["r12"]), ("pre-mortem", "Часть абонентов уйдёт к операторам, у которых ИИ входит в тариф бесплатно, доплата станет причиной оттока", ["A0"])]),
        ({"id": "O4", "title": "Закрыть направление", "kind": "close", "thesis": "Свернуть МегаНейросети, перевести абонентов на партнёрские ИИ-сервисы и сократить команду", "basis": ["r16", "r24", "r22"], "loses_if": "платящая аудитория растёт быстрее, чем уходит к бесплатным сервисам", "what_changes": "данные о платящих и марже выше точки окупаемости", "portfolio": "освобождает ресурсы, но оставляет абонентов без собственного ИИ-предложения"},
         [("skeptic", "Выход отдаёт растущий рынок конкурентам: охват ИИ-сервисов 55% населения и вырос вдвое за год", ["r23"]), ("pre-mortem", "Сворачивание без данных о марже не докажет, что направление убыточно", ["V4"])]),
    ]
    for data, objections in specs:
        data = {**data, "basis": [b.ids[k] for k in data["basis"]]}
        _option(project, data, [(by, text, [b.ids[k] for k in refs]) for by, text, refs in objections])
    model.set_model(project, _model_def(b))
    first_run = model.run(project, tag="seed")
    workflow.open_gate(project, "G3", None)
    _quiet(project, "G3", "G3 да O1 O2 O3 варианты собраны демо-командой")
    workflow.advance(project)

    b.claim("K2", {"text": _model_text(first_run), "type": "calculation", "calc_id": f"model-{first_run['id']}", "depends_on": ["A1", "A2", "A3", "A4", "A6", "A7", "A8", "r2"]})
    b.claim("V3", {"text": "Вывод: пакет «ИИ в тарифе» лидирует по модели, но только на допущениях о доплате и числе абонентов", "type": "hypothesis", "confidence": 0.4, "depends_on": ["K2", "A6", "H4"], "topic": "conclusion"}, {"verify_plan": "пилот пакета на 5 000 абонентов, 28 дней"})
    b.claim("V5", {"text": "Вывод: стартовая цена пакета 99 ₽ в месяц, её проверяем пилотом", "type": "hypothesis", "confidence": 0.35, "depends_on": ["V3"], "topic": "conclusion"}, {"verify_plan": "пилот с двумя ценами: 99 ₽ и 79 ₽"})
    _write_docs(b, "initial")
    problems = _strategy_problems(project)
    if problems:
        raise OsnError("Стратегия не прошла проверки:\n- " + "\n- ".join(problems))
    workflow.open_gate(project, "G4", None)
    _quiet(project, "G4", "G4 да O3 ответственный demo-seed")
    workflow.advance(project)
    commitments = [
        {"what": "Запросить у владельца продукта число подписчиков «Про» и конверсию из «Лайт»", "owner": "demo-seed", "due": "2026-12-05", "check_at": "2026-12-05", "metric": "получены ли числа", "threshold": "да", "expected": "числа подтвердят или опровергнут допущения A1 и A2", "review_if": "конверсия вне диапазона 2–10%", "claims": [b.ids["A1"], b.ids["A2"]]},
        {"what": "Собрать 10 интервью с закупщиками малого бизнеса", "owner": "demo-seed", "due": "2027-01-31", "check_at": "2026-12-20", "metric": "число интервью, где называют готовность платить", "threshold": ">= 4 из 10", "expected": "4 из 10 назовут оплату счёта за ИИ рабочим расходом", "review_if": "меньше 2 из 10", "claims": [b.ids["H2"]]},
        {"what": "Провести пилот пакета «ИИ в тарифе» на 5 000 абонентов", "owner": "demo-seed", "due": "2027-03-31", "check_at": "2027-01-15", "metric": "доля подключивших платный пакет через 28 дней", "threshold": ">= 8%", "expected": "подключат 8% абонентов группы", "review_if": "подключение ниже 4% или два оператора дают ИИ в тарифе бесплатно", "claims": [b.ids["A6"], b.ids["A7"]]},
    ]
    for item in commitments:
        review.add_commitment(project, item)

    _review_event(b, rows, by_url)
    trustmap.build(project)
    atomic_write(project.outputs / "cost.md", cost.report_md(project))
    return {"project": SLUG, "claims": len(project.current("claims")), "sources": len(project.current("sources")), "reviews": len(project.records("reviews")), "stage": workflow.load(project)["stage"]}


def _expires(days: int) -> str:
    from datetime import date, timedelta

    return (date(2026, 11, 14) + timedelta(days=days)).isoformat()


def _ingest(project: Project, url: str, group: list[dict]) -> str:
    quotes = "\n".join(r["quote"] for r in group)
    text = f"Сохранённые фрагменты страницы {url}, снято 14.11.2026. Страница не скачивалась.\n{quotes}\n"
    record = sources.ingest_bytes(project, text.encode("utf-8"), ".txt", group[0]["title"], "primary" if any(h in url for h in PRIMARY_HOSTS) else "secondary", url, group[0]["published"])
    project.append("sources", {**record, "accessed_at": CAPTURED, "offline": True})
    return record["id"]


def _write_docs_for_research(b: Builder) -> None:
    b.lenient = True
    text = _docs(b, "initial")["situation.md"]
    b.lenient = False
    atomic_write(b.project.outputs / "situation.md", text)


def _strategy_problems(project: Project) -> list[str]:
    from . import checks

    return checks.failures(project, "strategy")


def _review_event(b: Builder, rows: list[dict], by_url: dict[str, list[dict]]) -> None:
    project = b.project
    url = rows[12]["url"]
    beeline = [r for r in rows if r["n"] in EVENT_ROWS]
    source_id = _ingest(project, url, beeline)
    for row in beeline:
        text, topic, expiry, value, unit = FACTS[row["n"]]
        b.claim(f"F{row['n']}", {"text": text, "type": "fact", "source_id": source_id, "quote": row["quote"], "value": value, "unit": unit, "topic": topic, "expires": _expires(expiry)})
    verify.run(project)
    old = dict(b.ids)
    new_a0 = _supersede(project, old["A0"], {"text": "Билайн включает 300 токенов в месяц в подписку без доплаты, значит ИИ в базовом пакете оператора уже есть", "type": "fact", "source_id": source_id, "quote": beeline[0]["quote"], "value": 300, "unit": "токен/мес", "topic": "price", "depends_on": [], "expires": _expires(90)})
    verify.run(project)
    b.ids["A0"] = new_a0["id"]
    new_a6 = _supersede(project, old["A6"], {"text": "Допущение с учётом бесплатных токенов Билайна: абоненты соглашаются доплатить за ИИ в тарифе 59 ₽ в месяц (диапазон 29–89 ₽)", "type": "assumption", "value": 59, "unit": "руб/чел/мес", "confidence": 0.3, "depends_on": [b.ids["F13"], old["r10"], old["r12"]]})
    claims_mod.update(project, new_a6["id"], range=[29, 89], verify_plan="пилот пакета на 5 000 абонентов, 28 дней, цена 59 ₽ и 79 ₽")
    b.ids["A6"] = new_a6["id"]
    result = review.run(project, note="новый факт: Билайн начисляет 300 токенов в месяц без доплаты")
    if result["leader_changed"]:
        raise OsnError("Демо-пересмотр сменил лидера модели: подберите числа допущений, стратегия под нового лидера не пишется.")
    _quiet(project, "R", "R да пересмотр принят, формулировки обновлены демо-командой")
    run = model.last_run(project)
    stale = [c for c in project.current("claims").values() if c["status"] == "stale" and not c.get("superseded_by")]
    plans = {"K2": None, "H3": None, "V3": None, "V5": None}
    inverse = {v: k for k, v in old.items()}
    for claim in stale:
        key = inverse.get(claim["id"])
        if key not in plans:
            raise OsnError(f"Пересмотр затронул неожиданное утверждение {claim['id']}.")
    new = {}
    new["K2"] = _supersede(project, old["K2"], {"text": _model_text(run), "type": "calculation", "calc_id": f"model-{run['id']}", "depends_on": [b.ids[k] for k in ("A1", "A2", "A3", "A4", "A6", "A7", "A8")] + [b.ids["r2"]]})
    b.ids["K2"] = new["K2"]["id"]
    new["H3"] = _supersede(project, old["H3"], {"text": "Платный агрегатор выдержит бесплатных Алису, GigaChat и ИИ в базе у Билайна только нишевой пользой: оплата, VPN, выбор моделей", "type": "hypothesis", "confidence": 0.45, "depends_on": [old["r16"], old["r24"], b.ids["A0"]]})
    claims_mod.update(project, new["H3"]["id"], verify_plan="сравнить причины подключения в пилоте и в опросе абонентов")
    b.ids["H3"] = new["H3"]["id"]
    new["V3"] = _supersede(project, old["V3"], {"text": "Вывод: пакет «ИИ в тарифе» всё ещё лидирует по модели, но отрыв сократился после бесплатных токенов Билайна", "type": "hypothesis", "confidence": 0.35, "depends_on": [b.ids["K2"], b.ids["A6"], old["H4"]], "topic": "conclusion"})
    claims_mod.update(project, new["V3"]["id"], verify_plan="пилот пакета на 5 000 абонентов, 28 дней")
    b.ids["V3"] = new["V3"]["id"]
    new["V5"] = _supersede(project, old["V5"], {"text": "Вывод: стартовая цена пакета 59 ₽ в месяц, её проверяем пилотом", "type": "hypothesis", "confidence": 0.3, "depends_on": [b.ids["V3"]], "topic": "conclusion"})
    claims_mod.update(project, new["V5"]["id"], verify_plan="пилот с двумя ценами: 59 ₽ и 79 ₽")
    b.ids["V5"] = new["V5"]["id"]
    _write_docs(b, "revised")
    problems = review.checks.doc_problems(project) + review.checks.doc_problems(project, ["strategy.md"])
    if problems:
        raise OsnError("Документы после пересмотра не сходятся:\n- " + "\n- ".join(problems))
    review.finish(project)
