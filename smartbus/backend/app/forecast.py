"""Sadə və izah olunan tələbat proqnozu.

Üsul: eyni saatın son N gün üzrə orta tələbatı, bugünün son müşahidə olunan saatının
tarixi ortalamadan sapmasına (ratio, 0.7-1.3 aralığında məhdud) vurulur.
Gələcəyə baxış (look-ahead) yoxdur: yalnız target_hour-dan əvvəlki saatlar istifadə olunur.
"""
from __future__ import annotations

RATIO_MIN, RATIO_MAX = 0.7, 1.3


def forecast_demand(history: dict[int, dict[int, float]], today: dict[int, float],
                    target_hour: int, max_days: int = 7) -> dict:
    """history: {gün_ofseti(1=dünən): {saat: sərnişin}}, today: {saat: sərnişin}."""
    days = sorted(d for d in history if d >= 1)[:max_days]
    samples = [history[d][target_hour] for d in days if target_hour in history[d]]

    past_today = {h: v for h, v in today.items() if h < target_hour}   # look-ahead qadağası

    if not samples:
        if past_today:
            last = past_today[max(past_today)]
            return {"hour": target_hour, "value": int(round(last)), "method": "last_observed",
                    "ratio": 1.0, "samples": 0}
        return {"hour": target_hour, "value": 0, "method": "no_data", "ratio": 1.0, "samples": 0}

    base = sum(samples) / len(samples)
    ratio = 1.0
    prev = target_hour - 1
    if prev in past_today:
        prev_hist = [history[d][prev] for d in days if prev in history[d]]
        if prev_hist:
            mean_prev = sum(prev_hist) / len(prev_hist)
            if mean_prev > 0:
                ratio = min(max(past_today[prev] / mean_prev, RATIO_MIN), RATIO_MAX)
    return {"hour": target_hour, "value": int(round(base * ratio)),
            "method": "moving_average_trend", "ratio": ratio, "samples": len(samples)}
