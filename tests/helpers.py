import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


class Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osn-"))
        for name in ("bin", "tools", "config", ".agents", ".claude", "demo"):
            shutil.copytree(REPO / name, self.tmp / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(REPO / ".mcp.json", self.tmp / ".mcp.json")
        (self.tmp / "projects").mkdir()
        self.env = {**os.environ, "OSNOVANIE_ROOT": str(self.tmp)}
        self.env.pop("OSNOVANIE_PROJECT", None)
        self.env.pop("OSNOVANIE_DEV", None)

    def osn(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(self.tmp / "bin/osnovanie"), *args], capture_output=True, text=True, env=self.env, input=stdin, cwd=self.tmp)

    def ok(self, *args):
        result = self.osn(*args)
        if result.returncode:
            raise AssertionError(f"{args}: {result.stdout}\n{result.stderr}")
        return result.stdout

    def js(self, *args):
        return json.loads(self.ok("--json", *args))

    def hook(self, payload, strict=False):
        command = [sys.executable, str(self.tmp / "tools/hooks/claude_hook.py")] + (["strict"] if strict else [])
        return subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, env=self.env, cwd=self.tmp)

    def say(self, prompt):
        result = self.hook({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        assert result.returncode == 0, result.stderr
        return result.stdout

    def source(self, text, name="doc.txt", kind="secondary"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return self.js("source", "add", str(path), "--title", name, "--kind", kind)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def full_path(box: Sandbox, slug="demo"):
    """Минимальный проект от запроса до точки G4."""
    box.ok("new", slug, "--question", "Что делать с продуктом?")
    box.ok("brief", "set", "--question", "q", "--goal", "g", "--recipient", "r", "--constraints", "c", "--data", "d")
    box.ok("gate", "open", "G1")
    box.say("G1 да")
    box.ok("advance")
    for i in range(3):
        box.ok("question", "add", f"Вопрос {i}?", "--changes", "выбор варианта")
        box.ok("source", "plan", f"https://example.com/{i}", "--why", "нужно")
    box.ok("budget", "set", "5")
    box.ok("gate", "open", "G2")
    box.say("G2 да")
    box.ok("advance")
    sources = [box.source(f"Подписка стоит {590 + i * 100} рублей в месяц по тарифу номер {i}.", f"s{i}.txt", "primary") for i in range(3)]
    claims = []
    for i, source in enumerate(sources):
        quote = f"Подписка стоит {590 + i * 100} рублей в месяц"
        claims.append(box.js("claim", "add", "--json", json.dumps({"text": f"Цена {590 + i * 100} рублей в месяц", "type": "fact", "source_id": source["id"], "quote": quote, "value": 590 + i * 100, "unit": "руб/чел"}))["id"])
    for text in ("Гипотеза раз", "Гипотеза два"):
        claims.append(box.js("claim", "add", "--json", json.dumps({"text": text, "type": "hypothesis", "depends_on": [claims[0]]}))["id"])
    box.ok("verify")
    for number in box.js("question", "list"):
        box.ok("question", "answer", number["id"], "--claims", claims[0])
    outputs = box.tmp / "projects" / slug / "outputs"
    (outputs / "situation.md").write_text(f"# Что известно\n\nЦена 590 рублей [{claims[0]}].\n\n# Чего мы не знаем\n\nСпрос неизвестен.\n", "utf-8")
    box.ok("advance")
    specs = [("O1", "keep", "Оставить тариф прежним и ничего не менять"), ("O2", "change", "Поднять цену и добавить новые модели"), ("O3", "close", "Свернуть направление полностью и перевести людей")]
    for oid, kind, thesis in specs:
        box.ok("options", "add", "--json", json.dumps({"id": oid, "title": oid, "kind": kind, "thesis": thesis, "basis": [claims[0]], "loses_if": "если", "what_changes": "что", "portfolio": "не задевает другие продукты"}))
        box.ok("objection", "add", oid, "--by", "skeptic", "--text", "сильное возражение")
        box.ok("objection", "add", oid, "--by", "closure-advocate" if oid == "O2" else "pre-mortem", "--text", "второе возражение")
    model = {
        "params": {"price": {"label": "цена", "unit": "руб/чел", "low": 400, "base": 590, "high": 900, "claim": claims[0]}, "users": {"label": "люди", "unit": "чел", "low": 80, "base": 100, "high": 130}},
        "constants": {"cost": {"value": 30000, "unit": "руб"}},
        "options": {"O1": "price * users", "O2": "price * users * 1.2 - cost", "O3": "price * users * 0"},
        "output_unit": "руб",
    }
    box.ok("model", "set", "--json", json.dumps(model))
    box.ok("model", "run")
    box.ok("gate", "open", "G3")
    box.say("G3 да O1 O2")
    box.ok("advance")
    return claims


def write_strategy(box: Sandbox, claims, slug="demo"):
    outputs = box.tmp / "projects" / slug / "outputs"
    ref = f"[{claims[0]}]"
    (outputs / "product.md").write_text(f"# Продукт\n\nВариант: O1\n\n## Аудитория\n\nТекст {ref}.\n\n## Альтернативы\n\nТекст.\n\n## Направление\n\nДиагноз: текст.\n\n## Отказы\n\n- не делаем А, теряем Б\n- не делаем В, теряем Г\n\n## Экономика\n\nЦена 590 рублей {ref}.\n\n## Возможности\n\n| CAP1 | доступ | есть | {ref} |\n", "utf-8")
    (outputs / "communication.md").write_text(f"# Коммуникация\n\nВариант: O1\n\n## Аудитория\n\nТекст.\n\n## Барьер\n\nЦелевое поведение: подключить тариф. Главный барьер: тревога из-за цены ошибки и привычка к старому способу.\n\n## Ключевое сообщение\n\nТекст.\n\n## Подтверждение\n\nТекст {ref}.\n\n## Каналы\n\nТекст.\n\n## Обещания\n\n| P1 | доступ | CAP1 | сейчас | {ref} |\n", "utf-8")
    (outputs / "validation.md").write_text("# Проверка\n\nВариант: O1\n\n## Ожидаемое изменение\n\nТекст.\n\n## С чем сравниваем\n\nКонтрольная группа.\n\n## Срок\n\n28 дней.\n\n## Нежелательные последствия\n\n- жалобы\n- отток\n", "utf-8")
    (outputs / "monday.md").write_text(f"# Понедельник\n\nРешение O1 {ref}.\n", "utf-8")


_SEEDED = None
SLUG = "meganeuroseti-ref"


def seeded() -> Sandbox:
    """Эталонный проект собирается один раз за прогон, каждому тесту отдаётся своя копия."""
    global _SEEDED
    if _SEEDED is None:
        _SEEDED = Sandbox()
        _SEEDED.ok("demo", "seed")
    box = Sandbox()
    shutil.copytree(_SEEDED.tmp / "projects", box.tmp / "projects", dirs_exist_ok=True)
    return box


def tree_hashes(root: Path) -> dict[str, str]:
    import hashlib

    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}
