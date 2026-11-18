import json
import unittest

from helpers import SLUG, Sandbox, seeded


class DemoSeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = seeded()
        cls.project = cls.box.tmp / "projects" / SLUG

    @classmethod
    def tearDownClass(cls):
        cls.box.close()

    def records(self, name):
        path = self.project / "state" / f"{name}.jsonl"
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]

    def test_meta_marks_reference(self):
        meta = json.loads((self.project / "project.json").read_text("utf-8"))
        self.assertTrue(meta["reference"])
        self.assertIn("не прогон агента", meta["note"])

    def test_all_claim_types_and_quotes(self):
        status = self.box.js("--project", SLUG, "status")
        kinds = status["claims"] if "claims" in status else status
        text = json.dumps(kinds, ensure_ascii=False)
        for label in ("факт", "расчёт", "допущение", "гипотеза", "прогноз", "мнение"):
            self.assertIn(label, text)
        latest = {}
        for record in self.records("claims"):
            latest[record["id"]] = record
        facts = [c for c in latest.values() if c["type"] in {"fact", "opinion"} and not c.get("superseded_by")]
        self.assertEqual(len(facts), 28)
        self.assertTrue(all(c["status"] == "verified" for c in facts))
        self.assertTrue(all(c["author"] == "demo-seed" for c in latest.values()))

    def test_sources_are_offline_snapshots(self):
        sources = self.records("sources")
        offline = [s for s in sources if s.get("offline")]
        self.assertEqual(len({s["origin"] for s in offline}), 18)
        self.assertTrue(all(s["accessed_at"].startswith("2026-11-14") for s in offline))
        self.assertFalse((self.project / "state" / "failures.jsonl").exists() and self.records("failures"))

    def test_options_model_and_review(self):
        options = {}
        for record in self.records("options"):
            options[record["id"]] = record
        self.assertEqual(sorted(options), ["O1", "O2", "O3", "O4"])
        self.assertTrue(all(len(o["objections"]) >= 2 for o in options.values()))
        runs = self.records("model_runs")
        self.assertEqual([r["ranking"][0] for r in runs], ["O3", "O3"])
        self.assertLess(runs[1]["results"]["O3"]["base"], runs[0]["results"]["O3"]["base"])
        reviews = self.records("reviews")
        self.assertEqual(len(reviews), 1)
        self.assertIn("Билайн", reviews[0]["note"])
        kinds = [e["kind"] for e in self.records("events")]
        self.assertIn("review", kinds)
        self.assertTrue((self.project / "outputs" / "review-1.md").exists())

    def test_human_points_closed_by_seed_channel(self):
        gates = [r for r in self.records("gates") if r["action"] == "approve"]
        self.assertEqual({g["gate"] for g in gates}, {"G1", "G2", "G3", "G4", "R"})
        self.assertEqual({g["channel"] for g in gates}, {"demo-seed"})

    def test_commitments_and_checks(self):
        commitments = {}
        for record in self.records("commitments"):
            commitments[record["id"]] = record
        self.assertGreaterEqual(len(commitments), 3)
        self.assertTrue(all(c.get("check_at") for c in commitments.values()))
        self.assertTrue(self.box.js("--project", SLUG, "check"))

    def test_strategy_has_links(self):
        text = (self.project / "outputs" / "strategy.md").read_text("utf-8")
        self.assertRegex(text, r"\[c\d{3}")


class DemoSeedRepeat(unittest.TestCase):
    def test_second_run_needs_force(self):
        box = seeded()
        try:
            result = box.osn("demo", "seed")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--force", result.stderr)
            data = box.js("demo", "seed", "--force")
            self.assertEqual(data["project"], SLUG)
            self.assertEqual(data["stage"], "decided")
        finally:
            box.close()

    def test_without_source_file_fails(self):
        box = Sandbox()
        try:
            (box.tmp / "demo" / "meganeuroseti-sources.md").unlink()
            result = box.osn("demo", "seed")
            self.assertNotEqual(result.returncode, 0)
        finally:
            box.close()


if __name__ == "__main__":
    unittest.main()
