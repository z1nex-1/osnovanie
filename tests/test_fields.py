import json
import subprocess
import sys
import unittest

from helpers import REPO, Sandbox, full_path, write_strategy


class Fields(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.close)

    def test_expired_fact_becomes_stale_and_blocks_report(self):
        box = self.box
        box.ok("new", "demo", "--question", "q")
        source = box.source("Тариф конкурента стоит 299 рублей в месяц.", "c.txt", "primary")
        fact = box.js("claim", "add", "--json", json.dumps({"text": "Конкурент берёт 299 рублей", "type": "fact", "source_id": source["id"], "quote": "Тариф конкурента стоит 299 рублей в месяц", "expires": "2020-01-01"}))
        box.ok("verify")
        listing = {c["id"]: c for c in box.js("claim", "list")}
        self.assertEqual(listing[fact["id"]]["status"], "verified")
        self.assertIn("Истёк срок годности", box.ok("decide", "next"))
        bad = box.osn("claim", "add", "--json", json.dumps({"text": "x", "type": "hypothesis", "expires": "завтра"}))
        self.assertEqual(bad.returncode, 1)

    def test_confidence_must_be_probability(self):
        box = self.box
        box.ok("new", "demo", "--question", "q")
        bad = box.osn("claim", "add", "--json", json.dumps({"text": "Спрос вырастет", "type": "forecast", "confidence": 80}))
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("от 0 до 1", bad.stderr)
        box.ok("claim", "add", "--json", json.dumps({"text": "Спрос вырастет", "type": "forecast", "confidence": 0.4}))

    def test_assumption_keeps_range_and_verify_plan(self):
        box = self.box
        box.ok("new", "demo", "--question", "q")
        claim = box.js("claim", "add", "--json", json.dumps({"text": "Допущение: 30 % семей", "type": "assumption", "value": 30, "unit": "%", "confidence": 0.4, "range": [20, 45], "verify_plan": "двухнедельный замер"}))
        stored = box.js("claim", "show", claim["id"])
        self.assertEqual(stored["range"], [20, 45])
        self.assertEqual(stored["verify_plan"], "двухнедельный замер")
        newer = box.js("claim", "supersede", claim["id"], "--json", json.dumps({"text": "Допущение: 35 % семей", "value": 35, "range": [25, 45]}))
        self.assertEqual(box.js("claim", "show", newer["id"])["verify_plan"], "двухнедельный замер")
        for bad in ({"range": [45, 20]}, {"range": [20, 45], "value": 60}, {"verify_plan": ""}):
            result = box.osn("claim", "add", "--json", json.dumps({"text": "Допущение: доля", "type": "assumption", "value": 30, **bad}))
            self.assertNotEqual(result.returncode, 0, bad)

    def test_numbers_from_pdf_table_row_verify(self):
        box = self.box
        box.ok("new", "demo", "--question", "q")
        row = "Валовая прибыль за год, млн руб.         282           275           279             285"
        source = box.source(row + "\nПодключённые семейные группы на конец года   7 500   6 500   8 000   12 000", "brief.txt", "brief")
        ok = box.js("claim", "add", "--json", json.dumps({"text": "Без изменений прибыль 279 млн руб., цель 285", "type": "fact", "source_id": source["id"], "quote": row}))
        thousands = box.js("claim", "add", "--json", json.dumps({"text": "Цель — 12 000 подключённых групп", "type": "fact", "source_id": source["id"], "quote": "Подключённые семейные группы на конец года 7 500 6 500 8 000 12 000"}))
        wrong = box.js("claim", "add", "--json", json.dumps({"text": "Без изменений прибыль 280 млн руб.", "type": "fact", "source_id": source["id"], "quote": row}))
        box.ok("verify")
        listing = {c["id"]: c for c in box.js("claim", "list")}
        self.assertEqual(listing[ok["id"]]["status"], "verified")
        self.assertEqual(listing[thousands["id"]]["status"], "verified")
        self.assertNotEqual(listing[wrong["id"]]["status"], "verified")

    def test_option_needs_portfolio_and_confident_basis(self):
        box = self.box
        claims = full_path(box)
        box.ok("options", "add", "--json", json.dumps({"id": "O4", "title": "O4", "kind": "change", "thesis": "Продавать через партнёров малому бизнесу в регионах", "basis": [claims[3]], "loses_if": "если", "what_changes": "что"}))
        report = box.osn("check").stdout
        self.assertIn("O4: не указано влияние на портфель", report)
        self.assertIn(f"O4: у гипотез в основании нет уверенности: {claims[3]}", report)

    def test_withdrawn_option_leaves_state_but_stays_in_log(self):
        box = self.box
        claims = full_path(box)
        box.ok("options", "add", "--json", json.dumps({"id": "zz", "title": "t", "kind": "keep", "thesis": "проба формата", "basis": [claims[0]]}))
        self.assertNotEqual(box.osn("options", "withdraw", "zz", "--reason", " ").returncode, 0)
        box.ok("options", "withdraw", "zz", "--reason", "пробная запись")
        self.assertNotIn("zz", [o["id"] for o in box.js("options", "list")])
        self.assertNotEqual(box.osn("objection", "add", "zz", "--by", "skeptic", "--text", "x").returncode, 0)
        self.assertIn("option_withdrawn", box.ok("log"))

    def test_new_domains_after_decision_need_second_g2(self):
        box = self.box
        claims = full_path(box)
        write_strategy(box, claims)
        box.ok("gate", "open", "G4")
        box.say("G4 да O1 ответственный Анна")
        box.ok("advance")
        self.assertNotEqual(box.osn("gate", "open", "G3").returncode, 0)
        box.ok("gate", "open", "G2")
        box.say("G2 да")
        self.assertIn("decided", box.js("status")["stage"])

    def test_review_lists_new_claim_as_new_not_retracted(self):
        box = self.box
        full_path(box)
        source = box.source("Новый оператор запустил ИИ-ассистента 28 октября.", "n.txt", "secondary")
        fact = box.js("claim", "add", "--json", json.dumps({"text": "Новый оператор запустил ИИ-ассистента", "type": "fact", "source_id": source["id"], "quote": "Новый оператор запустил ИИ-ассистента 28 октября"}))
        box.ok("verify")
        report = box.js("review", "run", "--claims", fact["id"], "--note", "новость")["report"]
        text = (box.tmp / "projects" / "demo" / report).read_text("utf-8")
        self.assertIn(f"{fact['id']} новое", text)
        self.assertNotIn("отозвано: None", text)

    def test_commitment_records_expected_and_outcome(self):
        box = self.box
        claims = full_path(box)
        write_strategy(box, claims)
        box.ok("gate", "open", "G4")
        box.say("G4 да O1 ответственный Анна")
        box.ok("advance")
        base = {"what": "проверить", "owner": "Анна", "due": "2030-01-01", "metric": "ARPU", "threshold": ">500", "review_if": "падение", "claims": [claims[0]]}
        self.assertNotEqual(box.osn("commit", "add", "--json", json.dumps(base)).returncode, 0)
        record = box.js("commit", "add", "--json", json.dumps({**base, "expected": "ARPU 550 через квартал", "check_at": "2029-06-01"}))
        self.assertEqual(record["check_at"], "2029-06-01")
        box.ok("commit", "close", record["id"], "--outcome", "ARPU 520", "--hit", "no")
        track = box.js("commit", "track")
        self.assertEqual((track["closed"], track["hit"]), (1, 0))
        self.assertEqual(track["rows"][0]["expected"], "ARPU 550 через квартал")


class Facts(unittest.TestCase):
    def test_numbers_in_texts_match_repository(self):
        done = subprocess.run([sys.executable, str(REPO / "bin/osnovanie"), "facts", "check"], capture_output=True, text=True, cwd=REPO)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
