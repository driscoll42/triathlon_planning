"""Reproduce the TASK-021 / ADR-034 recalibration of the rolling-48h ANS caps.

Method (matched exclusion). A 48h window is 2-4 random library sessions. Among windows that already satisfy the
unchanged Tier-0 caps (48h TSS <= 295, 48h strain <= 260) take the share the LEGACY cap excluded
(``legacy ANS = sum_z T_z e^(0.65 (z-1)) > cap``, caps 700 charter / 450 CURRENT_ATHLETE). The new cap is the quantile of
the UNIFIED ANS (``triplan.physiology.load_model.ans_cost``) that excludes the same share. Run on three library slices
(default, default + hybrid, all); the adopted cap is the tightest slice rounded to the nearest 5.

Usage: ``python scripts/calibrate_ans_caps.py [--n 600000] [--seed 1]``  (reads data/workout_library.csv)
"""
import argparse
import math
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import sys  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "src"))

from triplan.library.loader import load_library  # noqa: E402
from triplan.physiology import load_model as lm  # noqa: E402

LEGACY_CAPS = {"charter": 700.0, "CURRENT_ATHLETE": 450.0}


def legacy_ans(z5, has_strides):
    w = [math.exp(0.65 * k) for k in range(5)]
    return sum(z5[k] * (1.0 if (k == 4 and has_strides) else w[k]) for k in range(5))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    df = load_library()
    z7 = df[["z1_min", "z2_min", "zx_min", "z3_min", "zy_min", "z4_min", "z5_min"]].fillna(0.0).to_numpy()
    strides = df["has_strides"].fillna(False).astype(bool).to_numpy()
    old = np.array([legacy_ans([z[0], z[1], z[3], z[5], z[6]], s) for z, s in zip(z7, strides)])
    new = df["ans_cost"].to_numpy()
    tss, strain = df["tss"].to_numpy(), df["mechanical_strain"].to_numpy()
    slices = {"default": df.index[df["lib_default"].astype(bool)].to_numpy(),
              "default+hybrid": df.index[(df["lib_default"].astype(bool)) | (df["lib_hybrid"].astype(bool))].to_numpy(),
              "all": df.index.to_numpy()}
    rng = np.random.default_rng(a.seed)
    print(f"adopted caps: charter 120, CURRENT_ATHLETE 55 (see ADR-034)")
    for name, pool in slices.items():
        k = rng.choice([2, 3, 4], a.n)
        s_t, s_s, s_o, s_n = (np.zeros(a.n) for _ in range(4))
        for j in range(4):
            ix = rng.choice(pool, a.n)
            m = k > j
            s_t[m] += tss[ix[m]]
            s_s[m] += strain[ix[m]]
            s_o[m] += old[ix[m]]
            s_n[m] += new[ix[m]]
        ok = (s_t <= 295.0) & (s_s <= 260.0)
        out = []
        for label, cap in LEGACY_CAPS.items():
            excl = float((s_o[ok] > cap).mean())
            out.append(f"{label}: legacy {cap:.0f} excluded {excl:.4%} -> unified cap {np.quantile(s_n[ok], 1 - excl):.1f}")
        print(f"[{name:15s}] windows ok {ok.mean():.1%} | " + " | ".join(out))


if __name__ == "__main__":
    main()
