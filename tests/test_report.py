import json
import re
import unittest

from helpers import REPO, SLUG, seeded

FIXTURE = (REPO / "tests/fixtures/report-megafon.md").read_text("utf-8")


class Report(unittest.TestCase):
    def setUp(self):
        self.box = seeded()
        self.box.ok("--project", SLUG, "report", "profile", "megafon-family")
        self.out = self.box.tmp / "projects" / SLUG / "outputs"

    def tearDown(self):
        self.box.close()

    def check(self, text):
        (self.out / "report.md").write_text(text, "utf-8")
        result = self.box.osn("--json", "--project", SLUG, "report", "check")
        return result.returncode, json.loads(result.stdout)

    def test_full_document_passes(self):
        code, result = self.check(FIXTURE)
        self.assertEqual(result["problems"], [])
        self.assertEqual(code, 0)
        self.assertEqual(result["covered"], result["total"])
        self.assertGreater(result["total"], 60)

    def test_missing_part_is_named(self):
        text = re.sub(r"## 3\. Польза и прибыль.*?(?=## 4\.)", "", FIXTURE, flags=re.S)
        code, result = self.check(text)
        self.assertEqual(code, 1)
        self.assertTrue(any("нет части 3" in p for p in result["problems"]))
        self.assertTrue(any("рандомизация по семье" in p for p in result["problems"]))

    def test_item_from_other_part_does_not_count(self):
        text = FIXTURE.replace("рандомизация по семье", "случайное распределение") + "\nДополнение: рандомизация по семье.\n"
        _, result = self.check(text)
        self.assertTrue(any("часть 3: рандомизация по семье" in p for p in result["problems"]))

    def test_summary_must_come_first(self):
        text = FIXTURE.replace("## Резюме", "## Вступление", 1)
        _, result = self.check(text)
        self.assertTrue(any("нет резюме" in p for p in result["problems"]))

    def test_number_without_reference(self):
        text = FIXTURE.replace("Нужно 6 чел.-мес.", "Нужно 6 чел.-мес. и 4 млн руб.")
        _, result = self.check(text)
        self.assertTrue(any("число без ссылки" in p for p in result["problems"]))

    def test_table_row_number_needs_reference(self):
        text = FIXTURE.replace("| Q4 | прибыль | бюджет |", "| Q4 | прибыль 285 млн руб. | бюджет |")
        _, result = self.check(text)
        self.assertTrue(any("строка таблицы" in p for p in result["problems"]))

    def test_strategy_stage_sees_report(self):
        (self.out / "report.md").write_text(FIXTURE.replace("## 5. Ресурсы, риски и решение", "## 5. Прочее"), "utf-8")
        output = self.box.osn("--project", SLUG, "check").stdout
        self.assertIn("нет части 5", output)

    def test_build_html(self):
        (self.out / "report.md").write_text(FIXTURE, "utf-8")
        info = self.box.js("--project", SLUG, "report", "build")
        page = (self.out / "report.html").read_text("utf-8")
        self.assertEqual(info["covered"], info["total"])
        self.assertIn("<h1>Семейная связь без сюрпризов</h1>", page)
        self.assertIn("Сверка с брифом", page)
        self.assertIn('id="note-c001"', page)
        self.assertEqual(page.count("<table>"), 3)
        self.assertNotRegex(page, r"""(?:src|href)\s*=\s*["']https?://""")

    def test_unknown_profile(self):
        result = self.box.osn("--project", SLUG, "report", "profile", "nope")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
