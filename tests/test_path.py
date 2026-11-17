import json
import unittest

from helpers import Sandbox, full_path, write_strategy


class FullPath(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.close)

    def test_end_to_end_gates_resume_review_map(self):
        box = self.box
        claims = full_path(box)
        write_strategy(box, claims)
        check = box.osn("check")
        self.assertEqual(check.returncode, 0, check.stdout)
        box.ok("gate", "open", "G4")
        self.assertIn("G4", box.ok("resume"))
        box.say("G4 да O1 ответственный Анна Руководитель")
        box.ok("advance")
        self.assertEqual(box.js("status")["stage"], "decided")
        self.assertTrue((box.tmp / "projects/demo/outputs/versions/v1/product.md").exists())
        box.ok("commit", "add", "--json", json.dumps({"what": "проверить", "owner": "Анна", "due": "2030-01-01", "metric": "ARPU", "threshold": ">500", "expected": "ARPU вырастет до 550", "review_if": "падение", "claims": [claims[0]]}))

        new_source = box.source("Подписка стоит 90 рублей в месяц по новому тарифу.", "new.txt", "scenario")
        box.ok("claim", "supersede", claims[0], "--json", json.dumps({"text": "Цена 90 рублей в месяц", "type": "fact", "source_id": new_source["id"], "quote": "Подписка стоит 90 рублей в месяц", "value": 90, "unit": "руб/чел"}))
        box.ok("verify")
        preview = box.js("review", "preview")
        self.assertEqual(set(preview["downstream"]), {claims[3], claims[4]})
        self.assertIn("O1", preview["options"])
        self.assertTrue(preview["commitments"])
        result = box.js("review", "run", "--note", "новая цена")
        self.assertTrue(result["leader_changed"] or result["ranking"])
        self.assertEqual(box.js("status")["awaiting"], "R")
        listing = {c["id"]: c for c in box.js("claim", "list")}
        self.assertEqual(listing[claims[3]]["status"], "stale")

        box.say("R да")
        problems = box.osn("review", "finish")
        self.assertEqual(problems.returncode, 2)
        self.assertIn(claims[0], problems.stderr)
        box.ok("map")
        page = (box.tmp / "projects/demo/outputs/trust-map.html").read_text("utf-8")
        self.assertIn("Неофициальный материал", page)
        self.assertNotIn("<script>alert", page)

    def test_agent_cannot_skip_gate(self):
        box = self.box
        box.ok("new", "demo", "--question", "q")
        self.assertEqual(box.osn("advance").returncode, 2)
        box.ok("brief", "set", "--question", "q", "--goal", "g", "--recipient", "r", "--constraints", "c", "--data", "d")
        box.ok("gate", "open", "G1")
        self.assertEqual(box.osn("advance").returncode, 2)
        denied = box.osn("gate", "approve", "G1")
        self.assertEqual(denied.returncode, 2)
        self.assertIn("человек", denied.stderr)

    def test_revise_keeps_stage_and_records_comment(self):
        box = self.box
        box.ok("new", "demo", "--question", "q")
        box.ok("brief", "set", "--question", "q", "--goal", "g", "--recipient", "r", "--constraints", "c", "--data", "d")
        box.ok("gate", "open", "G1")
        box.say("G1 правка: цель другая")
        status = box.js("status")
        self.assertEqual(status["stage"], "intake")
        self.assertIn("цель другая", box.ok("resume"))

    def test_pause_blocks_tools(self):
        box = self.box
        box.ok("new", "demo", "--question", "q")
        box.say("стоп")
        verdict = box.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "bin/osnovanie advance"}}, strict=True)
        self.assertIn("остановлена", verdict.stdout)
        allowed = box.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "bin/osnovanie status"}}, strict=True)
        self.assertEqual(allowed.stdout.strip(), "")
        box.say("продолжить")
        again = box.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "bin/osnovanie advance"}}, strict=True)
        self.assertEqual(again.stdout.strip(), "")

    def test_export_import_continues(self):
        box = self.box
        claims = full_path(box)
        archive = box.tmp / "out.tar.gz"
        box.ok("export", str(archive))
        other = Sandbox()
        self.addCleanup(other.close)
        other.ok("import", str(archive), "--name", "moved")
        other.ok("use", "moved")
        self.assertEqual(other.js("status")["stage"], "strategy")
        self.assertEqual(other.js("claim", "show", claims[0])["status"], "verified")


if __name__ == "__main__":
    unittest.main()


class StrategyChecks(unittest.TestCase):
    """Каждая проверка качества стратегии ловит свой дефект."""

    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.close)
        self.claims = full_path(self.box)
        write_strategy(self.box, self.claims)
        self.outputs = self.box.tmp / "projects" / "demo" / "outputs"

    def spoil(self, name, old, new):
        path = self.outputs / name
        text = path.read_text("utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), "utf-8")
        return self.box.osn("gate", "open", "G4")

    def test_defects(self):
        cases = [
            ("product.md", "- не делаем В, теряем Г\n", "", "меньше двух пунктов"),
            ("product.md", "Диагноз: текст.", "Текст.", "нет диагноза"),
            ("communication.md", "Главный барьер: тревога из-за цены ошибки и привычка к старому способу.", "", "барьер не раскрыт"),
            ("validation.md", "28 дней.", "Скоро.", "срок наблюдения без числа"),
            ("validation.md", "- отток\n", "", "меньше двух антиметрик"),
            ("validation.md", "Контрольная группа.", "Ни с чем.", "не указано, с чем сравниваем"),
            ("communication.md", "| P1 | доступ | CAP1 | сейчас |", "| P1 | доступ | CAP9 | сейчас |", "не связано ни с одной возможностью"),
            ("product.md", "Цена 590 рублей", "Цена 590 рублей без ссылки.\n\nЕщё 700 рублей", "число без ссылки"),
        ]
        for name, old, new, expected in cases:
            with self.subTest(expected):
                backup = (self.outputs / name).read_text("utf-8")
                result = self.spoil(name, old, new)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout + result.stderr)
                (self.outputs / name).write_text(backup, "utf-8")

    def test_monday_required(self):
        (self.outputs / "monday.md").unlink()
        result = self.box.osn("gate", "open", "G4")
        self.assertIn("monday.md", result.stdout + result.stderr)

    def test_weasel_words_reported(self):
        path = self.outputs / "product.md"
        path.write_text(path.read_text("utf-8") + "\nЭто значительный рост.\n", "utf-8")
        self.assertIn("«значительный» без числа", self.box.osn("check").stdout)
