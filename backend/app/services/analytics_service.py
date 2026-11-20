"""
Analyse capacitaire : variations semaine/mois et projection de saturation.

Projection linéaire sur 30 jours, uniquement pour les ressources cumulatives (volumétrie).
Pour CPU/RAM (ressources instantanées), une « date de saturation » n'a pas de sens :
`days_to_full` vaut None.
"""
import logging
import math
from datetime import timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Ressources dont l'usage s'accumule dans le temps (projection pertinente).
CUMULATIVE_UNITS = {"TB", "GB"}
# Au-delà, la projection n'a pas de sens opérationnel (pente ≈ bruit de mesure) : « stable ».
MAX_PROJECTION_DAYS = 3650


def _severity(usage_pct: Optional[float], days_to_full: Optional[int]) -> str:
    if (days_to_full is not None and days_to_full < 90) or (usage_pct is not None and usage_pct >= 90):
        return "CRITICAL"
    if (days_to_full is not None and days_to_full < 180) or (usage_pct is not None and usage_pct >= 80):
        return "WARNING"
    return "HEALTHY"


def _empty() -> Dict[str, Any]:
    return {
        "capacity_total_tb": None, "capacity_used_tb": None, "capacity_free_tb": None, "usage_pct": None,
        "w2w_diff_tb": None, "w2w_growth_pct": None, "mom_diff_tb": None, "mom_growth_pct": None,
        "growth_per_day_tb": None, "days_to_full": None, "severity": "HEALTHY", "points": 0,
    }


def _growth(curr: pd.Series, prev: pd.Series) -> tuple[Optional[float], Optional[float]]:
    if curr.empty or prev.empty:
        return None, None
    diff = float(curr.mean() - prev.mean())
    base = float(prev.mean())
    return round(diff, 2), (round(diff / base * 100, 2) if base > 0 else None)


class CapacityAnalyticsService:

    @staticmethod
    def calculate_trends(history_points: List[Dict[str, Any]], unit: str = "TB") -> Dict[str, Any]:
        """`history_points` : [{"timestamp": datetime, "used_tb": float, "total_tb": float}, ...]"""
        if not history_points:
            return _empty()

        df = pd.DataFrame(history_points)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        last = df.iloc[-1]
        total, used = float(last["total_tb"]), float(last["used_tb"])
        result = _empty()
        result.update(
            capacity_total_tb=round(total, 2),
            capacity_used_tb=round(used, 2),
            capacity_free_tb=round(max(0.0, total - used), 2),
            usage_pct=round(used / total * 100, 1) if total > 0 else None,
            points=len(df),
        )

        now = df["timestamp"].max()
        def window(a: int, b: int) -> pd.Series:
            return df[(df["timestamp"] > now - timedelta(days=b)) & (df["timestamp"] <= now - timedelta(days=a))]["used_tb"]

        result["w2w_diff_tb"], result["w2w_growth_pct"] = _growth(window(0, 7), window(7, 14))
        result["mom_diff_tb"], result["mom_growth_pct"] = _growth(window(0, 30), window(30, 60))

        month = df[df["timestamp"] > now - timedelta(days=30)]
        span_days = (month["timestamp"].max() - month["timestamp"].min()).total_seconds() / 86400 if len(month) else 0
        # Projection seulement avec assez de recul (≥ 3 jours, ≥ 5 points) et pour une ressource cumulative.
        if unit in CUMULATIVE_UNITS and len(month) >= 5 and span_days >= 3:
            x = (month["timestamp"] - month["timestamp"].min()).dt.total_seconds().to_numpy() / 86400
            y = month["used_tb"].to_numpy(dtype=float)
            try:
                slope, _ = np.polyfit(x, y, 1)
                if not math.isnan(slope):
                    result["growth_per_day_tb"] = round(float(slope), 4)
                    free = max(0.0, total - used)
                    if slope > 1e-4 and total > 0 and free / slope <= MAX_PROJECTION_DAYS:
                        result["days_to_full"] = int(free / slope)
            except (np.linalg.LinAlgError, ValueError) as exc:
                logger.warning("Projection capacitaire impossible : %s", exc)

        result["severity"] = _severity(result["usage_pct"], result["days_to_full"])
        return result
