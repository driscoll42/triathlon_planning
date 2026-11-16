---
id: ADR-016
type: adr
domain: general
title: "Scope the Startup Token Budget to the Charter's Read List"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: []
affects_services: ["general"]
one_liner: "Startup core = STATE_SUMMARY + LODE_MAP + INVARIANTS + TASKS + SPEC.md < 7,500 tokens; charter excluded; task specs live in docs/BACKLOG.md."
---

# `ADR-016`: Scope the Startup Token Budget to the Charter's Read List

## 1. Context & Problem
`ADR-001` set the < 7,500-token startup core as `GEMINI.md` + `STATE_SUMMARY.md` + `LODE_MAP.md` + `INVARIANTS.md` + `TASKS.md`. By 2026-11-16 `lodectx.py stats` reported 10,758 tokens (`TASK-030`). Two scoping errors made the measured set diverge from what an agent actually reads:
1. The charter (`GEMINI.md`, symlinked as `CLAUDE.md`, `.agents/{GEMINI,CLAUDE,AGENTS}.md`; ~3,070 tokens) is injected by the agent harness every session. Agents do not open it through progressive disclosure, and it is user-owned instructions, so the lineage engine cannot trim it.
2. `SPEC.md` is item 5 of the charter's §1 mandatory read list but was not counted.
3. `TASKS.md` (~3,450 tokens) carried full multi-paragraph task specs for 20+ items.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep the charter in the counted set.** The four docs would have to fit in ~4,400 tokens, which means cutting `INVARIANTS.md` and `SPEC.md` content that is load-bearing (hardware and safety rules).
2. **Option B (Rejected): Raise the budget.** This defeats `ADR-001`'s purpose.
3. **Option C (ADOPTED): Count exactly the charter's §1 read list, and move task specs to an on-demand `docs/BACKLOG.md`.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `docs/lode.json` (`startup_files`), `scripts/lodectx.py` (`DEFAULT_CONFIG`, `LODE_MAP.md` header rendered from config), `docs/TASKS.md`, `docs/BACKLOG.md`.
- **Locked Parameters / Contract:** Startup core = `docs/STATE_SUMMARY.md` + `docs/LODE_MAP.md` + `docs/INVARIANTS.md` + `docs/TASKS.md` + `SPEC.md`, and it must stay < 7,500 tokens (`chars / 4`). `TASKS.md` holds one line per open item. Full specs go in `docs/BACKLOG.md`, which is read on demand for the task being started and deleted on close. The charter is budgeted separately by its owner.
- **Enforcement Mechanism:** `python scripts/lodectx.py stats` (PASS/FAIL).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `lodectx.py stats` on 2026-11-16 measured 10,968 tokens (FAIL) before this change and 6,758 tokens (PASS) after it.
- **Supersedes:** None. This refines the scope of `ADR-001`'s locked contract; the rest of `ADR-001` stands.

## 5. Consequences & Trade-Offs
- **Positive:** The measured set matches what agents read. There are ~700 tokens of headroom, and `TASKS.md` growth is bounded to one line per item.
- **Negative / Operational Cost:** Task specs now live in two places (index + backlog), and both must be updated when tasks open or close.
- **When to Revisit:** If the charter's §1 read list changes, or the charter itself grows past ~4,000 tokens.
