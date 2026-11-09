from __future__ import annotations

import ast
import hashlib
import operator
import random

from .claims import resolve_chain
from .store import OsnError, PolicyError, Project, now, read_json, write_json

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
SAMPLES = 4000


def parse_unit(text: str | None) -> dict[str, int]:
    if not text or text == "1":
        return {}
    dims: dict[str, int] = {}
    top, *rest = text.replace(" ", "").split("/")
    for name in top.split("*"):
        if name and name != "1":
            dims[name] = dims.get(name, 0) + 1
    for name in rest:
        dims[name] = dims.get(name, 0) - 1
    return {k: v for k, v in dims.items() if v}


def unit_text(dims: dict[str, int]) -> str:
    up = [k + (str(v) if v > 1 else "") for k, v in sorted(dims.items()) if v > 0]
    down = [k + (str(-v) if v < -1 else "") for k, v in sorted(dims.items()) if v < 0]
    return "*".join(up or ["1"]) + "".join("/" + d for d in down)


def _combine(left: dict, right: dict, sign: int) -> dict:
    out = dict(left)
    for key, value in right.items():
        out[key] = out.get(key, 0) + sign * value
    return {k: v for k, v in out.items() if v}


def _walk(node, env: dict[str, float], units: dict[str, dict] | None):
    if isinstance(node, ast.Expression):
        return _walk(node.body, env, units)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value), ({} if units is not None else None)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise OsnError(f"В формуле неизвестная величина {node.id}.")
        return env[node.id], (units[node.id] if units is not None else None)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value, unit = _walk(node.operand, env, units)
        return -value, unit
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left, lu = _walk(node.left, env, units)
        right, ru = _walk(node.right, env, units)
        if isinstance(node.op, ast.Div) and right == 0:
            raise OsnError("В формуле деление на ноль.")
        value = OPS[type(node.op)](left, right)
        unit = None
        if units is not None:
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if lu != ru:
                    raise PolicyError(f"Единицы не сходятся: {unit_text(lu)} и {unit_text(ru)} нельзя складывать.")
                unit = lu
            else:
                unit = _combine(lu, ru, 1 if isinstance(node.op, ast.Mult) else -1)
        return value, unit
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"min", "max"}:
        pairs = [_walk(arg, env, units) for arg in node.args]
        if units is not None and any(p[1] != pairs[0][1] for p in pairs):
            raise PolicyError("Единицы аргументов min и max не сходятся.")
        func = min if node.func.id == "min" else max
        return func(p[0] for p in pairs), pairs[0][1]
    raise OsnError("В формуле допустимы числа, величины, + - * / и min, max.")


def evaluate(formula: str, env: dict[str, float], units: dict[str, dict] | None = None):
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as error:
        raise OsnError(f"Формула не разобрана: {formula}") from error
    return _walk(tree, env, units)


def set_model(project: Project, model: dict) -> dict:
    for section in ("params", "options", "output_unit"):
        if section not in model:
            raise OsnError(f"В модели нет раздела {section}.")
    options = project.current("options")
    unknown = [o for o in model["options"] if o not in options]
    if unknown:
        raise OsnError(f"В модели варианты, которых нет в реестре: {', '.join(unknown)}.")
    params = _resolve(project, model)
    _check_units(model, params)
    write_json(project.state / "model.json", model)
    project.event("model_set", params=len(model["params"]), options=len(model["options"]))
    return model


def _resolve(project: Project, model: dict) -> dict[str, dict]:
    claims = project.current("claims")
    resolved = {}
    for name, spec in model["params"].items():
        for field in ("label", "unit", "base"):
            if field not in spec:
                raise OsnError(f"У величины {name} нет поля {field}.")
        low, base, high = spec.get("low", spec["base"]), spec["base"], spec.get("high", spec["base"])
        if not low <= base <= high:
            raise OsnError(f"У величины {name} нарушено low ≤ base ≤ high.")
        claim_id = spec.get("claim")
        link = None
        if claim_id:
            if claim_id not in claims:
                raise OsnError(f"Величина {name} ссылается на несуществующее {claim_id}.")
            link = resolve_chain(claims, claim_id)
            if link["status"] == "retracted":
                raise PolicyError(f"Величина {name} опирается на отозванное утверждение {link['id']}.")
            if link.get("value") is not None:
                if link.get("unit") and parse_unit(link["unit"]) != parse_unit(spec["unit"]):
                    raise PolicyError(f"У {name} единица {spec['unit']}, а у {link['id']} — {link['unit']}.")
                if base:
                    ratio = float(link["value"]) / base
                    low, base, high = low * ratio, float(link["value"]), high * ratio
                    if low > high:
                        low, high = high, low
                else:
                    base = float(link["value"])
        resolved[name] = {"label": spec["label"], "unit": spec["unit"], "low": low, "base": base, "high": high, "claim": link["id"] if link else None, "basis": spec.get("basis")}
    return resolved


def _check_units(model: dict, params: dict[str, dict]) -> None:
    env = {k: p["base"] for k, p in params.items()}
    units = {k: parse_unit(p["unit"]) for k, p in params.items()}
    for name, spec in model.get("constants", {}).items():
        env[name] = spec["value"]
        units[name] = parse_unit(spec.get("unit"))
    target = parse_unit(model["output_unit"])
    for option, formula in model["options"].items():
        _, unit = evaluate(formula, env, units)
        if unit != target:
            raise PolicyError(f"Формула {option} даёт {unit_text(unit)}, ожидается {model['output_unit']}. Проверьте периоды.")


def _triangular(rng: random.Random, spec: dict) -> float:
    low, base, high = spec["low"], spec["base"], spec["high"]
    return low if low == high else rng.triangular(low, high, base)


def _winner(values: dict[str, float]) -> str:
    return max(sorted(values), key=lambda k: values[k])


def run(project: Project, tag: str = "manual") -> dict:
    model = read_json(project.state / "model.json")
    if not model:
        raise OsnError("Модель не задана: osnovanie model set --file ...")
    params = _resolve(project, model)
    _check_units(model, params)
    constants = {k: v["value"] for k, v in model.get("constants", {}).items()}
    base_env = {**{k: p["base"] for k, p in params.items()}, **constants}
    base = {o: evaluate(f, base_env)[0] for o, f in model["options"].items()}

    fingerprint = hashlib.sha256(repr(sorted(params.items())).encode()).hexdigest()
    rng = random.Random(fingerprint)
    draws: dict[str, list[float]] = {o: [] for o in model["options"]}
    wins = dict.fromkeys(model["options"], 0)
    for _ in range(SAMPLES):
        env = {**{k: _triangular(rng, p) for k, p in params.items()}, **constants}
        sample = {o: evaluate(f, env)[0] for o, f in model["options"].items()}
        for option, value in sample.items():
            draws[option].append(value)
        wins[_winner(sample)] += 1
    ranking = sorted(base, key=lambda k: (-base[k], k))
    results = {}
    for option, values in draws.items():
        ordered = sorted(values)
        results[option] = {"base": round(base[option], 2), "p10": round(ordered[int(SAMPLES * 0.1)], 2), "p90": round(ordered[int(SAMPLES * 0.9)], 2), "p_best": round(wins[option] / SAMPLES, 3)}
    record = {
        "id": project.next_id("model_runs", "r"), "ts": now(), "tag": tag, "output_unit": model["output_unit"],
        "results": results, "ranking": ranking, "breakeven": _breakeven(model, params, constants, base_env, ranking[0]),
        "params": {k: {"base": round(p["base"], 4), "low": round(p["low"], 4), "high": round(p["high"], 4), "claim": p["claim"], "label": p["label"], "unit": p["unit"]} for k, p in params.items()},
        "assumption_share": _assumption_share(project, params),
    }
    project.append("model_runs", record)
    project.event("model_run", run=record["id"], ranking=ranking)
    return record


def _breakeven(model: dict, params: dict, constants: dict, base_env: dict, leader: str) -> list[dict]:
    flips = []
    for name, spec in params.items():
        if spec["low"] == spec["high"]:
            continue
        for bound in ("low", "high"):
            # С базового значения идём к границе и ищем первую смену лидера.
            start, end = spec["base"], spec[bound]
            if start == end:
                continue
            def leader_at(value: float, name: str = name) -> str:
                env = {**base_env, name: value}
                return _winner({o: evaluate(f, env)[0] for o, f in model["options"].items()})
            if leader_at(end) == leader:
                continue
            lo, hi = start, end
            for _ in range(40):
                mid = (lo + hi) / 2
                if leader_at(mid) == leader:
                    lo = mid
                else:
                    hi = mid
            flips.append({"param": name, "label": spec["label"], "unit": spec["unit"], "threshold": round(hi, 4), "direction": bound, "new_leader": leader_at(end), "base": round(spec["base"], 4)})
    return flips


def _assumption_share(project: Project, params: dict) -> float:
    claims = project.current("claims")
    if not params:
        return 0.0
    weak = 0
    for spec in params.values():
        claim = claims.get(spec["claim"]) if spec["claim"] else None
        if not claim or claim["status"] != "verified":
            weak += 1
    return round(weak / len(params), 3)


def last_run(project: Project) -> dict | None:
    runs = project.records("model_runs")
    return runs[-1] if runs else None
