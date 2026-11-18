import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from helpers import REPO

spec = importlib.util.spec_from_file_location("d13", REPO / "evals/d13/d13.py")
d13 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d13)

MATERIALS = "## s001. Тарифы\n\nПодписка стоит 590 рублей в месяц, пробный период 7 дней.\n"
AGENT = "# Продукт\n\nВариант: O2\n\nПодписка за 590 рублей в месяц [c001] даёт доступ к моделям.\nПроверить через bin/osnovanie check.\nПробный период 7 дней [c001, c002].\n"
CHAT = "Рекомендую поднять цену до 990 рублей в месяц, потому что спрос вырастет на 40 процентов.\nПодписка сейчас 590 рублей.\n"


class D13(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="d13-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old = d13.RUNS
        d13.RUNS = self.tmp
        self.addCleanup(setattr, d13, "RUNS", self.old)
        self.run_dir = self.tmp / "t1-meganeuroseti"
        self.run_dir.mkdir()
        (self.run_dir / "materials.md").write_text(MATERIALS, "utf-8")
        claims = {"c001": {"text": "Подписка 590 рублей", "type": "fact", "source_id": "s001", "quote": "Подписка стоит 590 рублей в месяц"},
                  "c002": {"text": "Пробный период неделя", "type": "fact", "source_id": "s001", "quote": "пробный период 7 дней"}}
        text = d13._footnotes(AGENT, claims, {"s001": {"title": "Тарифы"}})
        (self.run_dir / "agent.md").write_text(text, "utf-8")
        (self.run_dir / "chat-plain.md").write_text(CHAT, "utf-8")
        (self.run_dir / "chat-docs.md").write_text(CHAT, "utf-8")

    def test_footnotes_replace_internal_ids_with_quotes(self):
        text = (self.run_dir / "agent.md").read_text("utf-8")
        self.assertNotIn("c001", text)
        self.assertIn("[1, 2]", text)
        self.assertIn("[1] (факт) Подписка 590 рублей. Цитата: «Подписка стоит 590 рублей в месяц». Источник: Тарифы.", text)

    def test_blind_pairs_every_arm_and_keeps_key_apart(self):
        d13.cmd_blind(SimpleNamespace(task="t1-meganeuroseti", seed="1"))
        key = json.loads((self.run_dir / "key.json").read_text("utf-8"))
        self.assertEqual(sorted(key["pairs"]), ["docs", "plain"])
        for arm, pair in key["pairs"].items():
            self.assertEqual(sorted(pair.values()), ["agent", f"chat-{arm}"])
            for letter in "XY":
                text = (self.run_dir / "blind" / arm / f"{letter}.md").read_text("utf-8")
                self.assertNotIn("osnovanie", text.lower())
                self.assertNotIn("agent", text)
        self.assertIn("[plain]", (self.run_dir / "human.toml").read_text("utf-8"))

    def test_factcheck_finds_numbers_absent_from_materials(self):
        chat = d13.factcheck(self.run_dir, "chat-plain")
        self.assertEqual((chat["checked"], chat["traced"]), (2, 1))
        self.assertEqual(chat["untraced"][0]["missing"], ["990", "40"])
        agent = d13.factcheck(self.run_dir, "agent")
        self.assertEqual(agent["checked"], agent["traced"])

    def test_score_unblinds_checks_quotes_and_flags_order_dependence(self):
        d13.cmd_blind(SimpleNamespace(task="t1-meganeuroseti", seed="1"))
        pair = json.loads((self.run_dir / "key.json").read_text("utf-8"))["pairs"]["plain"]
        agent_letter = "X" if pair["X"] == "agent" else "Y"
        chat_letter = "Y" if agent_letter == "X" else "X"
        texts = {letter: (self.run_dir / "blind" / "plain" / f"{letter}.md").read_text("utf-8") for letter in "XY"}
        judges = self.run_dir / "judges" / "plain"
        judges.mkdir(parents=True)
        items = [i["id"] for i in d13.rubric()]
        for order, prefer in (("XY", agent_letter), ("YX", chat_letter)):
            scores = {i: {agent_letter: 8, chat_letter: 5, f"quote_{agent_letter}": texts[agent_letter].split("\n")[2][:40],
                          f"quote_{chat_letter}": "фразы нет в работе", "why": "-"} for i in items}
            scores["r5"] = {agent_letter: 4, chat_letter: 7, "why": "-"}
            (judges / f"sonnet-{order}.json").write_text(json.dumps({"judge": "sonnet", "family": "claude", "order": order, "scores": {**scores, "prefer": prefer, "why": "-"}}), "utf-8")
        result = d13.score_task(self.run_dir)["pairs"]["plain"]["sonnet"]
        self.assertEqual(result["agent"]["r1"], 8)
        self.assertEqual(result["chat-plain"]["r5"], 7)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["quotes"], [12, 24])
        report = d13.report({"t1-meganeuroseti": d13.score_task(self.run_dir)})
        self.assertIn("r5: агент 4, чат 7", report)

    def test_chat_prompt_carries_same_requirements_as_judges(self):
        prompt = d13.chat_prompt("Кому продавать?", "docs", MATERIALS)
        for item in d13.rubric():
            self.assertIn(item["ask"], prompt)
        self.assertIn("оставить как есть", prompt)
        self.assertIn("590 рублей", prompt)
        self.assertNotIn("590 рублей", d13.chat_prompt("Кому продавать?", "plain", MATERIALS))


if __name__ == "__main__":
    unittest.main()
