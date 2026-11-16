"""``WeekModel``: a buffered, sparse MILP builder for one training week (TASK-015, ADR-017).

Columns (lb / ub / cost / integrality) live in growable numpy buffers. Rows are buffered as CSR
(``starts`` / ``indices`` / ``values`` plus row bounds). Nothing touches HiGHS until
:meth:`WeekModel.build`, which performs ONE ``addCols`` and ONE ``addRows``. Rows added after the
build (Top-K cuts) go straight to the solver through :func:`add_row_to_highs`.

Row hygiene is centralized here so no builder has to repeat it: duplicate column indices are
summed, entries with ``|v| <= coef_tol`` are dropped, and an emptied row is discarded (or, when its
bounds are unsatisfiable, either raises or is kept for the solver to declare infeasible, per
``empty_row_policy``).

Soft goals are dimensionless: ``add_soft_goal(coeffs, target, scale, weight, ...)`` creates
non-negative deviation columns ``pos`` and ``neg`` with ``coeffs . x - pos + neg == target`` and cost
``weight / scale`` per unit, so the penalty is ``weight * |deviation| / scale``. Asymmetric weights
(``pos_weight`` / ``neg_weight``) express over- and under-shoot separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import highspy
import numpy as np

INF = float(highspy.kHighsInf)

Coeffs = Union[Mapping[int, float], Tuple[Sequence[int], Sequence[float]]]


class _Buf:
    """Append-only numpy buffer with capacity doubling."""

    def __init__(self, dtype, cap: int = 1024) -> None:
        self.a = np.empty(cap, dtype=dtype)
        self.n = 0

    def _grow(self, need: int) -> None:
        cap = len(self.a)
        while cap < need:
            cap *= 2
        if cap != len(self.a):
            self.a = np.resize(self.a, cap)

    def push(self, v) -> None:
        self._grow(self.n + 1)
        self.a[self.n] = v
        self.n += 1

    def extend(self, arr) -> None:
        arr = np.asarray(arr)
        self._grow(self.n + len(arr))
        self.a[self.n:self.n + len(arr)] = arr
        self.n += len(arr)

    @property
    def view(self) -> np.ndarray:
        return self.a[:self.n]


@dataclass
class SoftGoal:
    """Handle to the deviation columns of one soft goal."""
    name: str
    pos: int
    neg: int
    scale: float
    pos_cost: float
    neg_cost: float


def _pick(v, k: int):
    if isinstance(v, (list, tuple, np.ndarray)):
        return v[k]
    return v


class WeekModel:
    """Buffered column/row model with a bulk HiGHS build."""

    def __init__(self, empty_row_policy: str = "raise", coef_tol: float = 1e-9) -> None:
        if empty_row_policy not in ("raise", "keep"):
            raise ValueError("empty_row_policy must be 'raise' or 'keep'")
        self.empty_row_policy = empty_row_policy
        self.coef_tol = coef_tol
        # columns
        self._lb = _Buf(np.float64)
        self._ub = _Buf(np.float64)
        self._cost = _Buf(np.float64)
        self._int = _Buf(np.bool_)
        self.names: Dict[str, int] = {}
        # rows (CSR)
        self._starts = _Buf(np.int64)
        self._starts.push(0)
        self._idx = _Buf(np.int32, 4096)
        self._val = _Buf(np.float64, 4096)
        self._rlb = _Buf(np.float64)
        self._rub = _Buf(np.float64)
        self.row_names: List[Optional[str]] = []
        self.built = False

    # ------------------------------------------------------------------ columns
    @property
    def num_cols(self) -> int:
        return self._lb.n

    @property
    def num_rows(self) -> int:
        return self._rlb.n

    def add_var(self, name: Optional[str] = None, lb: float = 0.0, ub: float = 1.0, cost: float = 0.0,
                integer: bool = False) -> int:
        """Append one column and return its index."""
        if self.built:
            raise RuntimeError("WeekModel is already built; add columns before build()")
        idx = self._lb.n
        self._lb.push(lb)
        self._ub.push(ub)
        self._cost.push(cost)
        self._int.push(bool(integer))
        if name is not None:
            if name in self.names:
                raise KeyError(f"duplicate column name {name!r}")
            self.names[name] = idx
        return idx

    def add_vars(self, count: int, lb=0.0, ub=1.0, cost=0.0, integer: bool = False,
                 names: Optional[Sequence[str]] = None) -> np.ndarray:
        """Bulk-append ``count`` columns (scalar or per-column lb/ub/cost); returns their indices."""
        if self.built:
            raise RuntimeError("WeekModel is already built; add columns before build()")
        start = self._lb.n
        self._lb.extend(np.broadcast_to(np.asarray(lb, dtype=np.float64), (count,)))
        self._ub.extend(np.broadcast_to(np.asarray(ub, dtype=np.float64), (count,)))
        self._cost.extend(np.broadcast_to(np.asarray(cost, dtype=np.float64), (count,)))
        self._int.extend(np.full(count, bool(integer)))
        if names is not None:
            for k, nm in enumerate(names):
                if nm in self.names:
                    raise KeyError(f"duplicate column name {nm!r}")
                self.names[nm] = start + k
        return np.arange(start, start + count, dtype=np.int64)

    def col(self, name: str) -> int:
        return self.names[name]

    def set_cost(self, col: int, cost: float) -> None:
        self._cost.a[col] = cost

    def costs(self) -> np.ndarray:
        return self._cost.view.copy()

    # ------------------------------------------------------------------ rows
    def add_row(self, coeffs: Coeffs, lb: float, ub: float, name: Optional[str] = None) -> Optional[int]:
        """Append a row ``lb <= sum coeffs <= ub``. Returns its index, or None when it collapsed."""
        if isinstance(coeffs, Mapping):
            cols = np.fromiter(coeffs.keys(), dtype=np.int64, count=len(coeffs))
            vals = np.fromiter(coeffs.values(), dtype=np.float64, count=len(coeffs))
        else:
            cols = np.asarray(coeffs[0], dtype=np.int64).ravel()
            vals = np.asarray(coeffs[1], dtype=np.float64).ravel()
            if cols.shape != vals.shape:
                raise ValueError("coefficient columns and values differ in length")
        if len(cols):
            uniq, inv = np.unique(cols, return_inverse=True)  # sorted, duplicate-summing
            if len(uniq) != len(cols):
                summed = np.zeros(len(uniq), dtype=np.float64)
                np.add.at(summed, inv, vals)
                cols, vals = uniq, summed
            else:
                order = np.argsort(cols, kind="stable")
                cols, vals = cols[order], vals[order]
            keep = np.abs(vals) > self.coef_tol
            cols, vals = cols[keep], vals[keep]
        if len(cols) == 0:
            infeasible = lb > self.coef_tol or ub < -self.coef_tol
            if not infeasible:
                return None
            if self.empty_row_policy == "raise":
                raise ValueError(f"Empty row with infeasible bounds [{lb}, {ub}]")
        self._idx.extend(cols.astype(np.int32))
        self._val.extend(vals)
        self._starts.push(self._idx.n)
        self._rlb.push(lb)
        self._rub.push(ub)
        self.row_names.append(name)
        return self._rlb.n - 1

    def csr(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(starts[m], indices, values, row_lb, row_ub) of the buffered rows."""
        m = self._rlb.n
        return (self._starts.view[:m].astype(np.int32), self._idx.view.copy(), self._val.view.copy(),
                self._rlb.view.copy(), self._rub.view.copy())

    # ------------------------------------------------------------------ soft goals
    def declare_soft_goal(self, name: str, scale: float, weight: float, pos_weight: Optional[float] = None,
                          neg_weight: Optional[float] = None, ub: float = INF) -> SoftGoal:
        """Create the deviation columns of a soft goal; add its row later with :meth:`goal_row`."""
        return self.declare_soft_goals([name], [scale], weight, pos_weight, neg_weight, ub)[0]

    def declare_soft_goals(self, names: Sequence[str], scales: Sequence[float], weight=1.0, pos_weight=None,
                           neg_weight=None, ub: float = INF, layout: str = "paired") -> List[SoftGoal]:
        """Create deviation columns for several goals. ``layout='paired'`` orders ``pos, neg`` per goal;
        ``'blocked'`` creates every ``pos`` then every ``neg`` (the legacy hybrid column order).
        ``weight`` / ``pos_weight`` / ``neg_weight`` may be scalars or per-goal sequences."""
        n = len(names)
        if any(s <= 0 for s in scales):
            raise ValueError("soft goal scales must be positive")
        pc = [float(_pick(weight if pos_weight is None else pos_weight, k)) / scales[k] for k in range(n)]
        nc = [float(_pick(weight if neg_weight is None else neg_weight, k)) / scales[k] for k in range(n)]
        if layout == "blocked":
            pos = [self.add_var(f"{names[k]}_pos", 0.0, ub, pc[k]) for k in range(n)]
            neg = [self.add_var(f"{names[k]}_neg", 0.0, ub, nc[k]) for k in range(n)]
        else:
            pos, neg = [], []
            for k in range(n):
                pos.append(self.add_var(f"{names[k]}_pos", 0.0, ub, pc[k]))
                neg.append(self.add_var(f"{names[k]}_neg", 0.0, ub, nc[k]))
        return [SoftGoal(names[k], pos[k], neg[k], float(scales[k]), pc[k], nc[k]) for k in range(n)]

    def goal_row(self, goal: SoftGoal, coeffs: Coeffs, target: float) -> Optional[int]:
        """Row ``coeffs . x - pos + neg == target`` for a declared goal (deviation = coeffs . x - target)."""
        if isinstance(coeffs, Mapping):
            cols = list(coeffs.keys()) + [goal.pos, goal.neg]
            vals = list(coeffs.values()) + [-1.0, 1.0]
        else:
            cols = list(np.asarray(coeffs[0]).ravel()) + [goal.pos, goal.neg]
            vals = list(np.asarray(coeffs[1]).ravel()) + [-1.0, 1.0]
        return self.add_row((cols, vals), target, target, name=f"goal:{goal.name}")

    def add_soft_goal(self, coeffs: Coeffs, target: float, scale: float, weight: float, name: str,
                      pos_weight: Optional[float] = None, neg_weight: Optional[float] = None,
                      ub: float = INF) -> SoftGoal:
        """Declare a dimensionless soft goal and add its row in one step."""
        goal = self.declare_soft_goal(name, scale, weight, pos_weight, neg_weight, ub)
        self.goal_row(goal, coeffs, target)
        return goal

    # ------------------------------------------------------------------ build
    def build(self, h: "highspy.Highs") -> None:
        """Push all buffered columns and rows into ``h`` with one ``addCols`` and one ``addRows``."""
        n, m = self.num_cols, self.num_rows
        if n:
            h.addCols(n, self._cost.view.copy(), self._lb.view.copy(), self._ub.view.copy(), 0,
                      np.zeros(n, dtype=np.int32), np.array([], dtype=np.int32), np.array([], dtype=np.float64))
            ints = np.flatnonzero(self._int.view).astype(np.int32)
            if len(ints):
                h.changeColsIntegrality(len(ints), ints,
                                        np.array([highspy.HighsVarType.kInteger] * len(ints)))
        if m:
            starts, indices, values, rlb, rub = self.csr()
            h.addRows(m, rlb, rub, len(indices), starts, indices, values)
        self.built = True

    # ------------------------------------------------------------------ adapters
    def hooks(self, x: Callable[[int, int], int], allowed: Callable[[int, int], bool]):
        """A ``safety_constraints.ModelHooks`` writing into this buffer (rows and columns stay buffered)."""
        from triplan.solver.safety_constraints import ModelHooks
        return ModelHooks(
            x=x,
            add_row=lambda coeff_map, lb, ub: self.add_row(coeff_map, lb, ub),
            add_var=lambda lb, ub, integer: self.add_var(None, lb, ub, 0.0, integer),
            allowed=allowed,
        )


def add_row_to_highs(h: "highspy.Highs", coeffs: Mapping[int, float], lb: float, ub: float,
                     tol: float = 1e-9) -> None:
    """Add one row directly to a built model (Top-K cuts). Zero entries are dropped."""
    items = sorted((c, v) for c, v in coeffs.items() if abs(v) > tol)
    h.addRow(float(lb), float(ub), len(items), np.array([c for c, _ in items], dtype=np.int32),
             np.array([v for _, v in items], dtype=np.float64))
