---
id: ADR-009
type: adr
domain: physiology
title: "Adopt Canonical 80/20 7-Zone Power System and Two-Point Critical Power Protocol"
status: ACTIVE
created_at: 2026-11-10
supersedes: []
validated_by: []
affects_services: ["physiology", "workouts", "optimizer"]
one_liner: "Enforce 80/20 7-zone power scale with Zone X/Y penalties and calibrate CP via 3m+12m protocol due to high W' from heavy lifting."
---

# `ADR-009`: Adopt Canonical 80/20 7-Zone Power System and Two-Point Critical Power Protocol

## 1. Context & Problem
Because the athlete engages in concurrent heavy barbell strength training (squats and deadlifts), their anaerobic work capacity ($W'$) is substantially elevated relative to pure endurance specialists. Standard single-effort aerobic proxy tests with fixed multipliers (such as the cycling ramp test at $0.75 \times \text{MAP}$ or un-cleared 20-minute time trials at $0.95 \times P_{20}$) draw heavily upon this enlarged anaerobic battery. Consequently, single-effort tests systematically overestimate sustainable aerobic threshold ($LT_2$) and Maximum Lactate Steady State ($MLSS$), leading to severe overtraining, premature glycogen depletion, and elevated orthopedic strain.

Furthermore, commercial 5-zone systems (such as Stryd 5-Zone or MyProCoach 5-Zone) define Zone 2 as spanning 80%–90% of CP. This improperly extends above the true first lactate threshold ($LT_1$), prescribing training in the moderate-intensity "grey zone" and defeating the polar/pyramidal 80/20 recovery architecture.

## 2. Considered Alternatives
1. **Option A (Fixed-Multiplier Ramp or 20-min TT):** Relying on $0.75 \times \text{MAP}$ or $0.95 \times P_{20}$. Rejected because the hybrid athlete's high anaerobic capacity ($W'$) inflates short-duration output, producing non-sustainable threshold targets.
2. **Option B (Commercial 5-Zone Scale - Stryd / MyProCoach):** Standard 5-zone power partitioning. Rejected because their Zone 2 extends past $LT_1$ into Zone X (80%–90% CP), causing chronic autonomic fatigue and blunting base volume.
3. **Option C (ADOPTED):** Calibrate Critical Power using the Two-Point Linear Work-Time Model ($P_3$ 180s + $P_{12}$ 720s) or Kolie Moore 35–45m MLSS baseline, and enforce the canonical **80/20 7-Zone Scale** with explicit penalization of Zone X (moderate drift) and Zone Y (threshold gap).

## 3. Decision & Production Invariant
- **Target Component / Path:** `SPEC.md`, `docs/domains/physiology.md`, `docs/domains/workouts.md`, `src/athlete_profile.py`, `src/classes.py`.
- **Locked Parameters / Contract:**
  1. **Two-Point CP Math:**
     $$CP = \frac{4 P_{12} - P_3}{3}, \quad W' = 180 \cdot (P_3 - CP)$$
  2. **80/20 7-Zone System:**
     - Zone 1 (Active Recovery): Run 50%–76% CP, Bike 50%–70% CP (ANS weight 0.0)
     - Zone 2 (Aerobic Endurance, Ceiling = $LT_1$): Run 76%–88% CP, Bike 70%–83% CP (ANS weight 0.0)
     - Zone X (Moderate Drift Grey Zone): Run 88%–94% CP, Bike 83%–91% CP (ANS weight 0.7 penalty)
     - Zone 3 (Sub-Threshold / Tempo, Ceiling = $LT_2$): Run 94%–100% CP, Bike 91%–100% CP (ANS weight 1.0)
     - Zone Y (Threshold Gap): Run 100%–103% CP, Bike 100%–102% CP (ANS weight 1.4 penalty)
     - Zone 4 (Supra-Threshold / $\text{VO}_2\text{max}$): Run 103%–120% CP, Bike 102%–110% CP (ANS weight 1.8)
     - Zone 5 (Anaerobic / Repetition): Run >120% CP, Bike >110% CP (ANS weight 3.5)
  3. **Positional Aero Rule:** Indoor/Aero CP must be tested in triathlon aero position; upright road hoods power is 3%–7% (10–20W) higher and must not be mixed without scaling.
  4. **Single Source of Truth:** Disable `Auto-CP` in Stryd and `Auto-FTP` in Garmin; lock manual CP integer identically across Stryd, TrainingPeaks, and optimizer profiles.
- **Enforcement Mechanism:** System invariants in `docs/INVARIANTS.md`, athlete profile validations, and workout parser zone mapping.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Monod & Scherrer two-parameter work-time linear formulation; empirical Kolie Moore MLSS baselining; 80/20 Endurance physiological demarcation (Fitzgerald & Warden).
- **Supersedes:** None. Establishes the authoritative multi-sport power and zone calibration baseline.

## 5. Consequences & Trade-Offs
- **Positive:** Eliminates anaerobic bias from threshold calculation; guarantees low-intensity training stays strictly below $LT_1$; penalizes accidental grey-zone drift.
- **Negative / Operational Cost:** Testing requires two all-out efforts separated by 30 minutes (or a dedicated 35–45m MLSS test), and manual synchronization across three platforms.
- **When to Revisit:** If athlete ceases heavy compound barbell lifting or switches to laboratory gas-exchange / blood lactate testing.
