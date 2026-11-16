---
id: ADR-004
type: adr
domain: physiology
title: "Decouple Aerobic TSS and Mechanical Strain with Asynchronous Tissue Remodeling"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
affects_services: ["physiology", "safety", "mpc"]
one_liner: "Model metabolic load (tau=35d) separately from mechanical strain (tau=120d) with an Orthopedic Dissociation Index <= 1.35."
---

# `ADR-004`: Decouple Aerobic TSS and Mechanical Strain with Asynchronous Tissue Remodeling

## 1. Context & Problem
Standard Performance Management Charts (PMC) model athlete fitness using a single exponentially weighted moving average of TSS ($\tau=42\text{ days}$). In multi-sport triathlon training, cycling and swimming generate high cardiovascular TSS with zero eccentric mechanical impact, while running induces severe ground-reaction impacts ($2.5-3.0\times$ body weight). Single-load models cause overuse injuries when high aerobic fitness outpaces tendon and bone collagen synthesis.

## 2. Considered Alternatives
1. **Option A (Monolithic TSS):** Single metric for all sports. Rejected because 100 TSS of cycling has completely different injury kinetics than 100 TSS of downhill intervals.
2. **Option B (Sport-Specific Uncoupled PMCs):** Three separate PMCs for Swim, Bike, Run. Rejected because aerobic cardiovascular adaptations transfer cross-sport, whereas peripheral tissue strain does not.
3. **Option C (ADOPTED):** Dual-load convolution model separating Aerobic TSS from Mechanical Strain with differential remodeling rates ($\tau_m=35d$ vs $\tau_c=120d$) and tracking the Orthopedic Dissociation Index ($ODI \le 1.35$).

## 3. Decision & Production Invariant
- **Target Component / Path:** `closed_loop_optimizer.py:CandidateWorkout`, `athlete_profile.py`.
- **Locked Parameters / Contract:** Cardiovascular time constant $\tau_m = 35\text{ days}$; connective tissue constant $\tau_c = 120\text{ days}$ ($\tau_c = 210\text{ days}$ for Masters athletes); $ODI = \text{Metabolic Load} / \text{Connective Tissue Load} \le 1.35$.
- **Enforcement Mechanism:** Daily receding-horizon constraint evaluation inside `ClosedLoopMPCController`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Maunder & Seiler durability research and Friel triathlete injury epidemiology.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Accurately detects and prevents "cardio-orthopedic mismatch" where high aerobic fitness destroys fragile connective tissue.
- **Negative / Operational Cost:** Candidate workouts must include both `aerobic_tss` and `mechanical_strain` attributes.
- **When to Revisit:** If wearable biomechanics sensors (e.g. Stryd impact shock or force plates) provide direct real-time tibial acceleration data.
