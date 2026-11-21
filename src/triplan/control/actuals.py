"""Actuals store for completed activities with 7-zone TiZ, TSS, and strain (TASK-025).

Persists realized activities (from ``.fit`` ingestion or manual logs) and advances
``AthletePhysiologicalState`` from actuals rather than planned workouts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from triplan.io.fit_ingest import ParsedActivity
from triplan.physiology.load_model import ZONES7
from triplan.physiology.state import AthletePhysiologicalState


def _empty_zones7() -> Dict[str, float]:
    return {z: 0.0 for z in ZONES7}


@dataclass
class CompletedActivity:
    """Normalized realized activity stored in the actuals ledger."""

    activity_id: str
    date: str  # YYYY-MM-DD
    sport: str
    duration_min: float
    tss: float = 0.0
    mechanical_strain: float = 0.0
    ans_cost: float = 0.0
    zones7_min: Dict[str, float] = field(default_factory=_empty_zones7)
    intensity_factor: Optional[float] = None
    avg_hr: Optional[float] = None
    efficiency_factor: Optional[float] = None
    decoupling_pct: Optional[float] = None
    workout_code: Optional[str] = None
    brick_run_min: float = 0.0
    brick_run_strain: float = 0.0
    brick_run_tss: float = 0.0
    source: str = "manual"

    def __post_init__(self) -> None:
        normalized = _empty_zones7()
        for z in ZONES7:
            normalized[z] = float(self.zones7_min.get(z, 0.0) or 0.0)
        self.zones7_min = normalized

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompletedActivity":
        return cls(
            activity_id=str(data["activity_id"]),
            date=str(data["date"]),
            sport=str(data["sport"]),
            duration_min=float(data.get("duration_min", 0.0) or 0.0),
            tss=float(data.get("tss", 0.0) or 0.0),
            mechanical_strain=float(data.get("mechanical_strain", 0.0) or 0.0),
            ans_cost=float(data.get("ans_cost", 0.0) or 0.0),
            zones7_min=dict(data.get("zones7_min") or {}),
            intensity_factor=data.get("intensity_factor"),
            avg_hr=data.get("avg_hr"),
            efficiency_factor=data.get("efficiency_factor"),
            decoupling_pct=data.get("decoupling_pct"),
            workout_code=data.get("workout_code"),
            brick_run_min=float(data.get("brick_run_min", 0.0) or 0.0),
            brick_run_strain=float(data.get("brick_run_strain", 0.0) or 0.0),
            brick_run_tss=float(data.get("brick_run_tss", 0.0) or 0.0),
            source=str(data.get("source", "manual")),
        )

    @classmethod
    def from_parsed_activity(
        cls,
        parsed: ParsedActivity,
        workout_code: Optional[str] = None,
        activity_id: Optional[str] = None,
    ) -> "CompletedActivity":
        """Converts a ``ParsedActivity`` from ``fit_ingest`` into a ``CompletedActivity``."""
        if parsed.start_time is None:
            raise ValueError(f"ParsedActivity {parsed.file_path} has no start_time")
        date_str = parsed.start_time.date().isoformat()
        aid = activity_id or f"{date_str}_{parsed.sport}_{parsed.start_time.strftime('%H%M%S')}_{parsed.file_path.stem}"
        return cls(
            activity_id=aid,
            date=date_str,
            sport=parsed.sport,
            duration_min=round(float(parsed.duration_sec) / 60.0, 2),
            tss=float(parsed.tss or 0.0),
            mechanical_strain=float(parsed.mechanical_strain or 0.0),
            ans_cost=float(parsed.ans_cost_val or 0.0),
            zones7_min=dict(parsed.zones7_min or {}),
            intensity_factor=parsed.intensity_factor,
            avg_hr=parsed.avg_hr,
            efficiency_factor=parsed.efficiency_factor,
            decoupling_pct=parsed.decoupling_pct,
            workout_code=workout_code,
            source="fit",
        )


class ActualsStore:
    """JSON-backed ledger of completed activities for closed-loop MPC state updates."""

    def __init__(self, path: Optional[Path] = None, auto_save: bool = True) -> None:
        self.path = Path(path) if path is not None else None
        self.auto_save = auto_save
        self._records: Dict[str, CompletedActivity] = {}
        if self.path is not None and self.path.exists():
            self.load()

    def __len__(self) -> int:
        return len(self._records)

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        items = raw.get("activities", []) if isinstance(raw, dict) else raw
        self._records = {}
        for item in items:
            act = CompletedActivity.from_dict(item)
            self._records[act.activity_id] = act

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self._records.values(), key=lambda a: (a.date, a.activity_id))
        payload = {"activities": [a.to_dict() for a in ordered]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def record(self, activity: CompletedActivity) -> CompletedActivity:
        """Upserts a completed activity by ``activity_id``."""
        self._records[activity.activity_id] = activity
        if self.auto_save and self.path is not None:
            self.save()
        return activity

    def record_parsed(
        self,
        parsed: ParsedActivity,
        workout_code: Optional[str] = None,
        activity_id: Optional[str] = None,
    ) -> CompletedActivity:
        act = CompletedActivity.from_parsed_activity(parsed, workout_code=workout_code, activity_id=activity_id)
        return self.record(act)

    def all(self) -> List[CompletedActivity]:
        return sorted(self._records.values(), key=lambda a: (a.date, a.activity_id))

    def for_date(self, date_str: str) -> List[CompletedActivity]:
        return [a for a in self.all() if a.date == date_str]

    def for_range(self, start_date: str, end_date: str) -> List[CompletedActivity]:
        return [a for a in self.all() if start_date <= a.date <= end_date]

    def rolling_cp_telemetry(
        self,
        sport: str,
        as_of_date: Optional[str] = None,
        window_days: int = 28,
    ) -> Dict[str, Optional[float]]:
        """Compute rolling decoupling (%) and 28-day EF change (%) from parsed actuals.

        * ``rolling_decoupling_pct``: mean ``decoupling_pct`` over steady aerobic
          sessions (``duration_min >= 30`` and ``intensity_factor <= 0.88`` when present)
          in ``(as_of - window_days, as_of]``.
        * ``rolling_ef_change_pct``: percentage change in mean ``efficiency_factor``
          between ``(as_of - window_days, as_of]`` and the prior baseline window
          ``(as_of - 2*window_days, as_of - window_days]``.
        """
        sport_norm = sport.strip().lower()
        sport_aliases = {
            "bike": ("bike", "cycling"),
            "cycling": ("bike", "cycling"),
            "run": ("run", "running"),
            "running": ("run", "running"),
            "swim": ("swim", "swimming"),
            "swimming": ("swim", "swimming"),
        }.get(sport_norm, (sport_norm,))

        matching = [a for a in self.all() if a.sport.strip().lower() in sport_aliases]
        if not matching:
            return {"rolling_decoupling_pct": None, "rolling_ef_change_pct": None}

        ref_date_str = as_of_date or matching[-1].date
        ref_date = date.fromisoformat(ref_date_str)
        cur_start = (ref_date - timedelta(days=window_days - 1)).isoformat()
        prev_start = (ref_date - timedelta(days=(2 * window_days) - 1)).isoformat()
        prev_end = (ref_date - timedelta(days=window_days)).isoformat()

        recent = [a for a in matching if cur_start <= a.date <= ref_date_str]
        baseline = [a for a in matching if prev_start <= a.date <= prev_end]

        steady_dec = [
            float(a.decoupling_pct)
            for a in recent
            if a.decoupling_pct is not None
            and a.duration_min >= 30.0
            and (a.intensity_factor is None or a.intensity_factor <= 0.88)
        ]
        if not steady_dec:
            steady_dec = [float(a.decoupling_pct) for a in recent if a.decoupling_pct is not None]
        rolling_dec = sum(steady_dec) / len(steady_dec) if steady_dec else None

        recent_ef = [float(a.efficiency_factor) for a in recent if a.efficiency_factor is not None and a.efficiency_factor > 0]
        base_ef = [float(a.efficiency_factor) for a in baseline if a.efficiency_factor is not None and a.efficiency_factor > 0]
        rolling_ef_chg: Optional[float] = None
        if recent_ef and base_ef:
            m_rec = sum(recent_ef) / len(recent_ef)
            m_base = sum(base_ef) / len(base_ef)
            if m_base > 0:
                rolling_ef_chg = ((m_rec - m_base) / m_base) * 100.0

        return {
            "rolling_decoupling_pct": rolling_dec,
            "rolling_ef_change_pct": rolling_ef_chg,
        }

    def daily_totals(self, date_str: str) -> Dict[str, Any]:
        """Aggregates realized TSS, run mechanical strain, ANS cost, and 7-zone TiZ for one day."""
        acts = self.for_date(date_str)
        tot_tss = 0.0
        bike_tss = 0.0
        run_tss = 0.0
        swim_tss = 0.0
        strength_tss = 0.0
        run_mech = 0.0
        total_mech = 0.0
        ans_cost = 0.0
        duration_min = 0.0
        zones7 = _empty_zones7()

        for a in acts:
            tot_tss += a.tss
            total_mech += a.mechanical_strain
            ans_cost += a.ans_cost
            duration_min += a.duration_min
            for z in ZONES7:
                zones7[z] += a.zones7_min.get(z, 0.0)

            sport = a.sport.strip().lower()
            if sport == "run":
                run_tss += a.tss
                run_mech += a.mechanical_strain
            elif sport == "bike":
                bike_tss += a.tss
            elif sport == "swim":
                swim_tss += a.tss
            elif sport == "strength":
                strength_tss += a.tss
            elif sport == "brick":
                r_tss = a.brick_run_tss
                if r_tss <= 0.0 and a.brick_run_min > 0.0 and a.duration_min > 0.0:
                    r_tss = a.tss * min(1.0, a.brick_run_min / a.duration_min)
                run_tss += r_tss
                bike_tss += max(0.0, a.tss - r_tss)
                run_mech += a.brick_run_strain

        return {
            "date": date_str,
            "tss": tot_tss,
            "run_mech": run_mech,
            "total_mech": total_mech,
            "bike_tss": bike_tss,
            "run_tss": run_tss,
            "swim_tss": swim_tss,
            "strength_tss": strength_tss,
            "ans_cost": ans_cost,
            "duration_min": duration_min,
            "zones7_min": zones7,
        }

    def advance_state(
        self,
        state: AthletePhysiologicalState,
        start_date: str,
        end_date: str,
    ) -> AthletePhysiologicalState:
        """Advances ``state`` day-by-day over ``[start_date, end_date]`` inclusive from actuals."""
        cur = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if end < cur:
            raise ValueError(f"end_date {end_date} < start_date {start_date}")
        while cur <= end:
            totals = self.daily_totals(cur.isoformat())
            state.step_day(
                daily_aerobic_tss=totals["tss"],
                daily_run_mech=totals["run_mech"],
                daily_bike_tss=totals["bike_tss"],
                daily_run_tss=totals["run_tss"],
                daily_swim_tss=totals["swim_tss"],
                daily_strength_tss=totals["strength_tss"],
            )
            cur += timedelta(days=1)
        return state
