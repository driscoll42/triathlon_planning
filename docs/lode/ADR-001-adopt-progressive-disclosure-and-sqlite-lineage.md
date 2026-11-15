---
id: ADR-001
type: adr
domain: general
title: "Adopt Progressive Disclosure & SQLite Lineage Architecture"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
affects_services: ["general", "optimizer", "mpc"]
one_liner: "Replace monolithic memory files with <7,500 token startup core and an append-only SQLite WAL lineage engine."
---

# `ADR-001`: Adopt Progressive Disclosure & SQLite Lineage Architecture

## 1. Context & Problem
As autonomous coding agents interact with long-running engineering projects, monolithic state documentation (`MEMORY.md` or unbounded changelogs) rapidly grows past 20,000+ tokens. This causes severe reasoning degradation, temporal state collisions (re-introducing discarded ideas or invalidated benchmark claims), and high token consumption on every turn.

## 2. Considered Alternatives
1. **Option A (Monolithic MEMORY.md):** Single markdown file containing all state. Rejected due to context bloating, compaction loss, and lack of temporal edge tracking.
2. **Option B (Vector Database RAG):** External vector DB. Rejected because similarity embeddings fail to distinguish active decisions from superseded historical notes.
3. **Option C (ADOPTED):** Progressive Disclosure Core (<7,500 tokens) backed by zero-dependency Python SQLite WAL + FTS5 BM25 lineage engine (`scripts/lodectx.py`).

## 3. Decision & Production Invariant
- **Target Component / Path:** `docs/`, `scripts/lodectx.py`, `docs/.context/lode.db`.
- **Locked Parameters / Contract:** Startup Core (`GEMINI.md`, `STATE_SUMMARY.md`, `LODE_MAP.md`, `INVARIANTS.md`, `TASKS.md`) must strictly stay under 7,500 estimated tokens (`chars / 4`).
- **Enforcement Mechanism:** Automated token budget check via `python scripts/lodectx.py stats`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Token metrics in `lodectx.py stats` demonstrating < 4,000 tokens for baseline startup core.
- **Supersedes:** None. Foundational architecture decision.

## 5. Consequences & Trade-Offs
- **Positive:** Agents only load active, verified decisions and domain manuals on-demand; zero temporal collisions.
- **Negative / Operational Cost:** Engineering decisions and heuristics must be scaffolded and ingested through `lodectx.py`.
- **When to Revisit:** If repository domain structure exceeds 25 domains or token budget needs recalibration.
