import json
import re
import unittest
import xml.dom.minidom

from helpers import SLUG, seeded
from osn import score, site
from osn.store import Project

EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["']https?://|url\(\s*["']?https?://|@import""", re.I)


class Deck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = seeded()
        cls.info = cls.box.js("--project", SLUG, "deck")
        cls.html = (cls.box.tmp / "projects" / SLUG / "outputs" / "deck.html").read_text("utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.box.close()

    def test_slide_count_and_format(self):
        self.assertTrue(12 <= self.info["slides"] <= 14)
        self.assertEqual(self.html.count('<section class="slide'), self.info["slides"])
        self.assertIn("size:1280px 720px", self.html)
        self.assertIn("page-break-after", self.html)
        self.assertIn("ArrowRight", self.html)

    def test_self_contained(self):
        self.assertIsNone(EXTERNAL.search(self.html))
        self.assertNotIn("<img", self.html)

    def test_palette(self):
        for color in ("#07100C", "#0CDC78", "#FFA717", "#FF5A64"):
            self.assertIn(color, self.html)

    def test_required_sections(self):
        for title in ("Задача", "Что знаем", "Чего не знаем", "Варианты и возражения", "Модель", "Рекомендация", "План проверки", "Пороги остановки", "Что изменилось при пересмотре", "Приложение"):
            self.assertIn(title, self.html)

    def test_every_footnote_has_quote_target(self):
        refs = set(re.findall(r'data-note="(c\d+)"', self.html))
        targets = set(re.findall(r'id="note-(c\d+)"', self.html))
        self.assertTrue(refs)
        self.assertEqual(refs - targets, set())
        body = self.html.split('id="note-', 1)[1]
        self.assertIn("<q>", body)

    def test_every_number_with_unit_has_reference(self):
        claims = {}
        for line in (self.box.tmp / "projects" / SLUG / "state" / "claims.jsonl").read_text("utf-8").splitlines():
            record = json.loads(line)
            claims[record["id"]] = record
        for match in re.finditer(r'<section class="slide[^>]*id="s(\d+)".*?</section>', self.html, re.S):
            number, slide = int(match.group(1)), match.group(0)
            if "Приложение" in slide[:600] or number in (1, 2):
                continue
            text = re.sub(r"<[^>]+>", " ", slide)
            if re.search(r"\d[\d\s]*\s*(?:%|₽)", text):
                self.assertIn('class="ref"', slide, f"слайд {number}")


class Score(unittest.TestCase):
    def setUp(self):
        self.box = seeded()
        self.project = Project(SLUG, self.box.tmp)
        self.addCleanup(self.box.close)

    def test_parts_sum_to_total(self):
        result = score.compute(self.project)
        self.assertEqual(sum(p["weight"] for p in result["parts"]), 100)
        self.assertEqual(result["score"], round(sum(p["points"] for p in result["parts"])))
        self.assertTrue(all(0 <= p["value"] <= 1 for p in result["parts"]))

    def test_seed_is_not_human(self):
        result = score.compute(self.project)
        human = next(p for p in result["parts"] if p["key"] == "human")
        self.assertEqual(human["points"], 0)
        self.assertTrue(any("demo-seed" in n for n in result["notes"]))
        self.assertLessEqual(result["score"], 85)

    def test_human_channel_adds_points(self):
        from osn import workflow

        state = workflow.load(self.project)
        for info in state["gates"].values():
            info["channel"] = "chat"
        workflow.save(self.project, state)
        result = score.compute(self.project)
        self.assertEqual(next(p for p in result["parts"] if p["key"] == "human")["points"], 15)
        self.assertEqual(result["score"], 100)

    def test_broken_link_lowers_score(self):
        from osn import claims

        before = score.compute(self.project)["score"]
        for claim in self.project.current("claims").values():
            if claim["type"] == "fact":
                claims.retract(self.project, claim["id"], "тест")
        after = score.compute(self.project)
        self.assertLess(after["score"], before)
        self.assertLess(next(p for p in after["parts"] if p["key"] == "numbers")["value"], 1.0)

    def test_cli_and_badge(self):
        box = seeded()
        try:
            out = box.tmp / "badge.svg"
            data = box.js("--project", SLUG, "score", "--badge", str(out))
            svg = out.read_text("utf-8")
            xml.dom.minidom.parseString(svg)
            self.assertIn(f'{data["score"]}/100', svg)
            self.assertIn("обоснованность", svg)
            self.assertNotIn("http://www.w3.org/1999/xlink", svg)
            self.assertIn("formula", data)
        finally:
            box.close()

    def test_badge_color_thresholds(self):
        self.assertIn("#0CDC78", score.badge(90))
        self.assertIn("#FFA717", score.badge(70))
        self.assertIn("#FF5A64", score.badge(30))


class Site(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = seeded()
        cls.out = cls.box.tmp / "site"
        cls.info = cls.box.js("--project", SLUG, "export", "site", "--out", str(cls.out))
        cls.data = json.loads((cls.out / "project.json").read_text("utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.box.close()

    def test_schema_and_sections(self):
        self.assertEqual(self.data["schema"], "osnovanie-site/1")
        for key in ("project", "brief", "claims", "edges", "sources", "options", "model", "human_points", "events", "reviews", "cost", "score", "layout", "commitments"):
            self.assertIn(key, self.data)
        self.assertTrue(self.data["project"]["reference"])

    def test_claims_carry_source_and_dates(self):
        fact = next(c for c in self.data["claims"] if c["id"] == "c002")
        self.assertTrue(fact["quote"])
        self.assertTrue(fact["source"]["domain"])
        self.assertTrue(fact["expires"])
        hypothesis = next(c for c in self.data["claims"] if c["type"] == "hypothesis" and c["confidence"])
        self.assertIsNotNone(hypothesis["confidence"])

    def test_edges_point_to_known_nodes(self):
        nodes = {n["id"] for n in self.data["layout"]["nodes"]}
        for edge in self.data["edges"]:
            self.assertIn(edge["from"], nodes)
            self.assertIn(edge["to"], nodes)
        self.assertTrue({"depends_on", "cites", "basis"} <= {e["kind"] for e in self.data["edges"]})

    def test_model_distribution(self):
        mc = self.data["model"]["monte_carlo"]
        self.assertEqual(set(mc), {"O1", "O2", "O3", "O4"})
        histogram = mc["O3"]["histogram"]
        self.assertEqual(sum(histogram["counts"]), self.data["model"]["samples"])
        self.assertEqual(len(histogram["edges"]), len(histogram["counts"]) + 1)
        self.assertTrue(self.data["model"]["breakeven"])

    def test_gates_events_reviews(self):
        self.assertEqual({g["gate"] for g in self.data["human_points"]}, {"G1", "G2", "G3", "G4", "R"})
        self.assertFalse(any(g["by_human"] for g in self.data["human_points"]))
        self.assertTrue(all("ts" in e for e in self.data["events"]))
        review = self.data["reviews"][0]
        self.assertGreaterEqual(len(review["waves"]), 2)
        self.assertEqual(review["waves"][0]["depth"], 1)

    def test_layout_bounds_and_determinism(self):
        for node in self.data["layout"]["nodes"]:
            for axis in "xyz":
                self.assertTrue(-1 <= node[axis] <= 1)
        ids = [n["id"] for n in self.data["layout"]["nodes"]]
        edges = [(e["from"], e["to"]) for e in self.data["edges"]]
        self.assertEqual(site.layout(ids, edges), site.layout(ids, edges))
        self.assertEqual(len({(n["x"], n["y"], n["z"]) for n in self.data["layout"]["nodes"]}), len(ids))

    def test_layout_small_cases(self):
        self.assertEqual(site.layout([], []), {})
        one = site.layout(["a"], [])
        self.assertEqual(list(one), ["a"])
        two = site.layout(["a", "b"], [("a", "b")])
        self.assertNotEqual(two["a"], two["b"])

    def test_old_export_still_needs_path(self):
        self.assertNotEqual(self.box.osn("--project", SLUG, "export", "site").returncode, 0)


if __name__ == "__main__":
    unittest.main()
