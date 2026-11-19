"""Интервалы для расчётов: доля по выборке (Вильсон) и разброс по Монте-Карло. Считает код, а не модель."""
from __future__ import annotations

import math
from statistics import NormalDist


def z_for(level: float) -> float:
    if not 0 < level < 1:
        raise ValueError("Уровень доверия должен быть между 0 и 1.")
    return NormalDist().inv_cdf(0.5 + level / 2)


def wilson(successes: int, trials: int, level: float = 0.9) -> tuple[float, float]:
    """Интервал Вильсона для доли: не вылезает за 0 и 1 и честен на малых выборках, где «8 из 10» ещё мало что значит."""
    if trials <= 0:
        raise ValueError("Число наблюдений должно быть больше нуля.")
    if not 0 <= successes <= trials:
        raise ValueError("Успехов не может быть меньше нуля или больше числа наблюдений.")
    z = z_for(level)
    p = successes / trials
    denom = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def percentile(ordered: list[float], q: float) -> float:
    """Перцентиль по уже отсортированной выборке, с линейной интерполяцией."""
    if not ordered:
        raise ValueError("Пустая выборка.")
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def share_spec(successes: int, trials: int, level: float = 0.9) -> dict:
    """Величина модели из выборки: база — наблюдаемая доля, края — интервал Вильсона."""
    low, high = wilson(successes, trials, level)
    return {"low": low, "base": successes / trials, "high": high, "interval": f"Вильсон {round(level * 100)}%, {successes} из {trials}"}
