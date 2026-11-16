---
id: EXP-XXX
type: experiment
domain: <domain>
title: "<Concise Benchmark or Experiment Title>"
status: QUEUED # QUEUED | RUNNING | COMPLETE_ADOPTED | COMPLETE_NULL_REJECTED | SUPERSEDED | NEEDS_RETEST
methodology_tier: TIER_1_ISOLATED # TIER_1_ISOLATED | TIER_2_CONTAMINATED | QUEUED_SPEC
created_at: YYYY-MM-DD
completed_at: null
supersedes: []
validates: []         # e.g., ["ADR-003"]
one_liner: "<1-sentence summary of the empirical finding and resulting policy (<160 chars)>"
---

# `EXP-XXX`: <Title>

## 1. Objective & Pre-Registered Hypothesis
- **Objective:** What algorithm, parameter, query, or system behavior is being measured?
- **Null Hypothesis ($H_0$):** Treatment has negligible effect or degrades tail latency/quality.
- **Alternative Hypothesis ($H_1$):** Treatment improves primary metric by >= X% without violating safety bounds.

## 2. Pre-Flight Ceteris-Paribus Hygiene Checklist
- [ ] **Environment Quiescence:** No competing background builds, indexers, or heavy processes running.
- [ ] **Cold vs. Warm State Control:** Explicitly control caches/connection pools between control and treatment.
- [ ] **Sample Size & Rigor:** Minimum $n \ge 5$ repetitions (report Median, IQR, Mean, and $\text{CI}_{95\%}$).

## 3. Non-Goals & Safety Bounds
- **DO NOT** mutate production data or commit temporary benchmark fixtures.
- **DO NOT** tune parameters mid-run to chase a passing score; report null or negative results honestly.

## 4. Control vs. Treatment Matrix & Test Corpus
- **Workload / Dataset:** Exact input dataset, fixture, or synthetic load profile.
- **Condition 1 (Control Baseline):** Exact command, commit, or config.
- **Condition 2 (Treatment A):** Exact command, commit, or config.

## 5. Telemetry Instruments & Sampling Commands
- **Primary Metrics:** Wall-clock throughput (ops/s), latency distribution (p50 / p95 / p99), memory RSS, or accuracy.
- **Measurement Command:** Exact reproducible CLI invocation.

## 6. Pre-Registered Go/No-Go Decision Rule
Adopt Treatment A in production (`ADR-XXX`) **ONLY IF** primary metric improves by >= X% AND tail constraint stays within bound. Otherwise retain Control Baseline and mark `COMPLETE_NULL_REJECTED`.

## 7. Empirical Results Table

| Condition | Reps ($n$) | Throughput | p50 / Mean | Tail (p95 / p99) | Memory / Resource | Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Cond 1 (Control)** | | | | | | Baseline |
| **Cond 2 (Treatment A)** | | | | | | |

## 8. Architectural Conclusion & Action Taken
- **Key Findings:** Numbered takeaways explaining *why* the system behaved this way.
- **Production Action:** Exact ADR created or config updated, and confirmation that scratch artifacts were cleaned up.
