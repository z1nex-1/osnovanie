import json
import os
import subprocess
import sys

import pytest

from helpers import REPO
from tools.osn import intervals, model, trustmap


def test_wilson_matches_reference_values():
    low, high = intervals.wilson(8, 100, level=0.95)
    assert low == pytest.approx(0.0411, abs=1e-4)
    assert high == pytest.approx(0.1500, abs=1e-4)


def test_wilson_stays_inside_zero_and_one_on_extremes():
    low, high = intervals.wilson(0, 12)
    assert low == 0.0 and 0 < high < 0.3
    low, high = intervals.wilson(12, 12)
    assert high == 1.0 and 0.7 < low < 1


def test_small_sample_gives_wide_interval():
    small = intervals.wilson(8, 10)
    large = intervals.wilson(800, 1000)
    assert small[1] - small[0] > 5 * (large[1] - large[0])


@pytest.mark.parametrize("args", [(1, 0), (-1, 10), (11, 10)])
def test_wilson_rejects_impossible_counts(args):
    with pytest.raises(ValueError):
        intervals.wilson(*args)


def test_percentile_interpolates():
    assert intervals.percentile([0.0, 10.0], 0.5) == 5.0
    assert intervals.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.9) == pytest.approx(4.6)


class FakeProject:
    def current(self, _name):
        return {}


def test_model_param_from_sample_uses_wilson_bounds():
    spec = {"model": {"params": {"conv": {"label": "доля подключений", "unit": "1", "successes": 8, "trials": 10, "base": 0}},
                      "options": {"A": "conv * 100", "B": "70"}, "output_unit": "1"}}
    params = model._resolve(FakeProject(), spec["model"])
    conv = params["conv"]
    assert conv["base"] == pytest.approx(0.8)
    assert (conv["low"], conv["high"]) == pytest.approx(intervals.wilson(8, 10))
    assert conv["interval"].startswith("Вильсон 90%")
    base, results, ranking, _ = model.simulate(spec["model"], params, {})
    assert ranking[0] == "A"
    # при «8 из 10» B выигрывает в заметной части розыгрышей: интервал не даёт выдать лидерство за уверенность
    assert 0.05 < results["B"]["p_best"] < 0.5


def test_breakeven_names_the_claim_and_trust_map_says_when_leader_loses():
    mdl = {"options": {"A": "conv * 100", "B": "70"}}
    params = {"conv": {"low": 0.5, "base": 0.8, "high": 0.95, "label": "доля подключений", "unit": "1", "claim": "c012"}}
    flips = model._breakeven(mdl, params, {}, {"conv": 0.8}, "A")
    assert len(flips) == 1 and flips[0]["claim"] == "c012"
    assert flips[0]["threshold"] == pytest.approx(0.7, abs=1e-3)
    text = trustmap.flip_text(flips[0], "A")
    assert "A теряет первое место" in text and "0.7" in text and "лидером станет B" in text
    table = trustmap._flip_table({"breakeven": flips, "ranking": ["A", "B"]})
    assert "c012" in table and "Когда A проигрывает" in table


def _opencode(event, payload):
    done = subprocess.run([sys.executable, str(REPO / "tools/hooks/opencode_hook.py"), event], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=REPO,
                          env={k: v for k, v in os.environ.items() if k not in {"OSNOVANIE_DEV", "OSNOVANIE_PROJECT"}})
    return json.loads(done.stdout)


def test_opencode_runs_under_the_same_policy():
    assert _opencode("tool", {"tool": "bash", "args": {"command": "bin/osnovanie status"}})["allow"]
    assert not _opencode("tool", {"tool": "bash", "args": {"command": "cat .env"}})["allow"]
    assert not _opencode("tool", {"tool": "bash", "args": {"command": "curl https://example.com"}})["allow"]
    assert not _opencode("tool", {"tool": "write", "args": {"filePath": "tools/osn/guard.py", "content": "x"}})["allow"]
    assert not _opencode("tool", {"tool": "webfetch", "args": {"url": "https://example.com"}})["allow"]
    assert not _opencode("tool", {"tool": "task", "args": {}})["allow"]
    assert not _opencode("tool", {"tool": "read", "args": {"filePath": "projects/x/materials/raw/s001.html"}})["allow"]
