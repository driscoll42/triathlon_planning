---
id: ADR-010
type: adr
domain: integrations
title: "Adopt Multi-Device Hardware Telemetry Rules and FIT Ingestion Invariants"
status: ACTIVE
created_at: 2026-11-10
supersedes: []
validated_by: []
affects_services: ["integrations", "safety", "physiology"]
one_liner: "Standardize FIT ingestion across Stryd Model 17, Garmin Enduro 3/Swim 2, Edge 530, and Tacx Neo 2T with strict dev-field and deduplication rules."
---

# `ADR-010`: Adopt Multi-Device Hardware Telemetry Rules and FIT Ingestion Invariants

## 1. Context & Problem
The athlete's telemetry pipeline spans five distinct hardware devices:
1. Stryd "Wind" Foot Pod (Model 17, 2019)
2. Garmin Enduro 3 Watch
3. Garmin Swim 2 Watch
4. Garmin Edge 530 Bike Computer
5. Tacx Neo 2T Smart Trainer

Without rigorous hardware-specific ingestion rules, catastrophic data corruption occurs:
- Garmin watches write native wrist running power to standard `.fit` `power` (inflated +20% to +30%), while Stryd power is stored in Connect IQ developer fields.
- Pool swim duration parsed from `session.total_elapsed_time` includes rest intervals at the wall, inflating active swim time and distorting sTSS.
- Concurrent recording on bike computer and watch produces duplicate FIT uploads for the same ride, artificially doubling weekly TSS.
- Tacx Neo 2T inductive sensor `left_right_balance` stream exhibits violent spurious spikes, falsely indicating orthopedic asymmetry.

## 2. Considered Alternatives
1. **Option A (Generic FIT Field Ingestion):** Parse standard record fields (`power`, `total_elapsed_time`) across all devices indiscriminately. Rejected due to Garmin native power contamination and wall-rest time inflation.
2. **Option B (Manual Device Tagging):** Require the athlete to manually tag and reformat each FIT file before ingestion. Rejected as error-prone and unscalable.
3. **Option C (ADOPTED):** Hardware-aware ingestion harness enforcing explicit Developer Field extraction, active length parsing for pool swims, 300-second multi-device timestamp deduplication, and sensor-specific exclusions.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/clean_file.py`, `src/sync_8020_to_tp.py`, `SPEC.md`, `docs/domains/integrations.md`.
- **Locked Parameters / Contract:**
  1. **Stryd Model 17 Extraction:** Read power strictly from Connect IQ developer field (`field.is_dev == True` and `field.name == 'Power'`). Ingest auxiliary fields `Form Power`, `Leg Spring Stiffness` (LSS, kN/m), and `Air Power`. Account for 0W `Air Power` on treadmill. Pair to Enduro 3 strictly via ANT+ (`FP-XXXXX`).
  2. **Garmin Enduro 3 & Swim 2 Ingestion:** In pool swims, active duration and sTSS must be computed strictly by summing `length` records where `length_type == 'active'`. Drills (`swim_stroke == 'drill'`) are excluded from SWOLF/stroke rate. Enduro 3 is the primary Unified Training Status hub.
  3. **Garmin Edge 530 & Tacx Neo 2T:** 1-second recording with zeros included. Overlapping rides between Edge 530 and Enduro 3 within $\le 300\text{s}$ are deduplicated to exactly one file. Neo 2T optical brake requires zero spin-down calibration; execute CP tests in Slope/Incline mode (+1.5% to +2.0%); ignore `left_right_balance` stream; keep connected to AC power during recovery.
- **Enforcement Mechanism:** *Planned* `src/fit_ingest.py` (`TASK-024`, validated by `EXP-005`). **Implementation status (2026-11-15 truth pass, `TASK-009`): not implemented** — `src/clean_file.py` is the 80/20 HTML workout-library scraper and does not parse recorded activity `.fit` files. Since `ADR-012`, Swim 2 is retired and devices do not dual-record; the 300s dedup rule applies to historical files.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Comparative `.fit` stream analysis between Garmin wrist power and Stryd Connect IQ developer field; empirical pool swim lap vs length analysis; Tacx Neo 2T inductive balance telemetry verification.
- **Supersedes:** None. Establishes the authoritative multi-device hardware ingestion architecture.

## 5. Consequences & Trade-Offs
- **Positive:** Guarantees 100% clean, uncontaminated power and biomechanical metrics across all 5 devices; prevents artificial double-counting of load.
- **Negative / Operational Cost:** Parser must handle variable developer field schemas and implement spatial-temporal deduplication windows.
- **When to Revisit:** If athlete replaces Stryd Model 17 with Next-Gen pod or upgrades bike computer / trainer hardware.
