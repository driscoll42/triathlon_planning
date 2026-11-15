---
id: PRD-XXX
type: prd
domain: <domain>
title: "<Short Descriptive Title>"
status: DRAFT # DRAFT | READY | IN_PROGRESS | COMPLETED | ABORTED
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
supersedes: []        # e.g., ["PRD-001"]
depends_on: []        # e.g., ["ADR-002", "EXP-001"]
affects_services: []  # e.g., ["api", "db"]
one_liner: "<Dense 1-sentence summary of the target state and why it is being built (<160 chars)>"
---

# Engineering PRD: <Title> (`PRD-XXX`)

## 1. Overview & Architectural Objective
One concise paragraph defining the subsystem, module boundary, and the exact target state.

## 2. Problem Statement & Empirical Evidence
- **Current Bottleneck / Risk:** Describe what is failing, slow, fragile, or missing right now.
- **Telemetry / Proof:** Cite exact metrics, logs, test failures, or profiling data—never vague hunches.

## 3. Target Goals (Measurable Outcomes)
- **[Performance / Efficiency]:** Target latency, throughput, memory, or resource budget.
- **[Security / Correctness]:** Invariants, type safety, auth boundaries, or data integrity guarantees.
- **[Reliability / Operability]:** Failure recovery, observability, and zero-regression guarantees.

## 4. Non-Goals (Strict Negative Scope — DO NOT TOUCH)
- **DO NOT** modify unrelated modules or break backward-compatible contracts without an explicit migration step.
- **DO NOT** place execution scratch notes in top-level `docs/` (use `docs/specs/` or `docs/archive/`).

## 5. System Invariants & Guardrails (`docs/INVARIANTS.md`)
- **State & Storage Boundaries:** How persistent vs. ephemeral state is handled.
- **Concurrency & Resource Bounds:** Threading, async, memory, or timeout limits.
- **Security & Access Control:** Principle of least privilege and input validation rules.

## 6. Deterministic Technical Requirements & Tasks
- [ ] **[Task 1 - MUST] First Requirement:**
  - *System Benefit:* Why this step is necessary.
  - *Implementation Spec:* Exact file paths, interfaces, schema changes, or CLI flags.
- [ ] **[Task 2 - MUST] Second Requirement:**
  - *System Benefit:* Why this step is necessary.
  - *Implementation Spec:* Exact commands, tests, and rollback path.

## 7. Linked Empirical Experiments (If Applicable)
- Validated or gated by: `EXP-XXX` (or `N/A`).

## 8. Acceptance Gates (Definition of Done)
- [ ] **Gate 1 (Rollback Ready):** Clean git state or backup verified prior to execution.
- [ ] **Gate 2 (Tests & Verification):** Unit/integration test suite passes 100%.
- [ ] **Gate 3 (Target Metric Verified):** Exact CLI command + expected output proving Section 3 goals.
- [ ] **Gate 4 (Lineage & Docs Synced):** `python3 scripts/lodectx.py ingest <this_file>` and `lodectx.py stats` pass.

## 9. Open Questions (Halt Gate)
> **Agent Rule:** If any checkbox below is unchecked `[ ]`, HALT and ask the user before executing Section 6.
- [x] All architectural ambiguities resolved.
