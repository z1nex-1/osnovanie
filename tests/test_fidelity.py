import json
import unittest

from helpers import SLUG, seeded


class Fidelity(unittest.TestCase):
    def setUp(self):
        self.box = seeded()
        self.addCleanup(self.box.close)
        self.project = self.box.tmp / "projects" / SLUG

    def latest(self, cid):
        found = None
        for line in (self.project / "state" / "claims.jsonl").read_text("utf-8").splitlines():
            record = json.loads(line)
            if record["id"] == cid:
                found = record
        return found

    def rewrite(self, cid, **changes):
        with open(self.project / "state" / "claims.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({**self.latest(cid), **changes}, ensure_ascii=False) + "\n")

    def fidelity(self):
        result = self.box.osn("--json", "--project", SLUG, "fidelity")
        return result.returncode, json.loads(result.stdout)

    def test_reference_project_is_clean(self):
        code, result = self.fidelity()
        self.assertEqual(code, 0)
        self.assertEqual(result["errors"], 0)
        self.assertGreaterEqual(result["facts_checked"], 20)

    def test_number_absent_from_quote_is_an_error(self):
        self.rewrite("c021", text="Месячный охват ИИ-сервисов в России в сентябре достиг 65% населения старше 12 лет")
        code, result = self.fidelity()
        self.assertEqual(code, 1)
        self.assertIn("числа 65 нет в цитате", json.dumps(result, ensure_ascii=False))

    def test_lost_hedge_is_a_warning(self):
        quote = self.latest("c014")["quote"]
        self.assertIn("перспективе", quote)
        self.rewrite("c014", quote="разработчик намеревается взимать деньги примерно с 2027 года", text="Сбер берёт деньги за GigaChat с 2027 года")
        _, result = self.fidelity()
        rows = [r for r in result["rows"] if r["where"] == "c014"]
        self.assertTrue(any("оговорка «примерно»" in r["what"] and r["level"] == "warning" for r in rows), rows)

    def test_assumption_presented_as_fact_blocks_strategy(self):
        path = self.project / "outputs" / "product.md"
        path.write_text(path.read_text("utf-8") + "\nАбоненты доплатят за пакет [c034].\n", "utf-8")
        code, result = self.fidelity()
        self.assertEqual(code, 1)
        self.assertTrue(any("подано как факт" in r["what"] for r in result["rows"]))
        check = self.box.osn("--project", SLUG, "check")
        self.assertIn("пересказ не искажает", check.stdout)
        self.assertNotEqual(check.returncode, 0)


if __name__ == "__main__":
    unittest.main()
