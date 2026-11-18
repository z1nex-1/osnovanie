from tools.osn.model import _breakeven


def test_new_leader_is_the_first_flip_not_the_far_bound():
    # при росте x лидер идёт A → B → C: точка перелома должна назвать B
    model = {"options": {"A": "10", "B": "x", "C": "3 * x - 30"}}
    params = {"x": {"low": 0.0, "base": 5.0, "high": 40.0, "label": "x", "unit": "1", "claim": None}}
    flips = _breakeven(model, params, {}, {"x": 5.0}, "A")
    high = [f for f in flips if f["direction"] == "high"]
    assert len(high) == 1
    assert high[0]["new_leader"] == "B"
    assert abs(high[0]["threshold"] - 10.0) < 1e-3
