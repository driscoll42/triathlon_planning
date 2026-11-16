---
id: ADR-XXX
type: adr
domain: <domain>
title: "<Active Verb + Concrete Decision>"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: YYYY-MM-DD
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: []      # e.g., ["EXP-005"]
affects_services: []  # e.g., ["core", "db"]
one_liner: "<1-sentence rule that agents MUST follow in this domain (<160 chars)>"
---

# `ADR-XXX`: <Title>

## 1. Context & Problem
What architectural trade-off, system constraint, or operational failure forced this decision? What was the previous state (if any), and why did it fail or prove suboptimal?

## 2. Considered Alternatives
1. **Option A (Rejected):** Summary of option A and the exact empirical or architectural reason it was rejected.
2. **Option B (Rejected):** Summary of option B and the exact empirical or architectural reason it was rejected.
3. **Option C (ADOPTED):** Summary of the winning pattern.

## 3. Decision & Production Invariant
State the exact, non-negotiable engineering rule:
- **Target Component / Path:** Exact module, path, service, or config file.
- **Locked Parameters / Contract:** Exact configuration, algorithm, schema rule, or API boundary.
- **Enforcement Mechanism:** Automated test, linter rule, CI check, or runtime assertion.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Cite `EXP-XXX` or concrete benchmark/test telemetry proving this decision.
- **Supersedes:** Cite `<OLD-ID>` (or `None`) and explain why the old approach must not be re-proposed.

## 5. Consequences & Trade-Offs
- **Positive:** What this guarantees for correctness, speed, or maintainability.
- **Negative / Operational Cost:** What complexity or constraint must be maintained as a result.
- **When to Revisit:** Under what future scale, hardware, or dependency change should this ADR be re-evaluated?
