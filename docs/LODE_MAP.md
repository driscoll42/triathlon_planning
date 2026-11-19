# Triathlon Workout & Season Planner — Lode Map & Progressive Disclosure Index (`docs/LODE_MAP.md`)

> **Startup Core (< 7,500 tokens, `ADR-016`):** `docs/STATE_SUMMARY.md` + `docs/LODE_MAP.md` + `docs/INVARIANTS.md` + `docs/TASKS.md` + `SPEC.md`. Agent charter (`CLAUDE.md`/`GEMINI.md`) is harness-loaded and budgeted separately; open-task specs: `docs/BACKLOG.md` (on demand).
> **On-Demand Domain Briefing:** Run `python3 scripts/lodectx.py brief <domain>` before modifying any subsystem.
> **Canonical Templates & Scaffolding:** `python3 scripts/lodectx.py init <prd|adr|exp|heur>` & `python3 scripts/lodectx.py ingest <path>`.

## 1. Domain Lode Routing Table
| Domain | Lode File | Briefing CLI | Scope |
| :--- | :--- | :--- | :--- |
| `general` | `docs/domains/general.md` | `python3 scripts/lodectx.py brief general` | Cross-cutting architecture, tooling, environment, and repository standards |
| `optimizer` | `docs/domains/optimizer.md` | `python3 scripts/lodectx.py brief optimizer` | HiGHS Mixed-Integer Linear Programming (MILP) solver (unified WeekModel engine), selection Top-K cuts, pruning and objective weighting |
| `mpc` | `docs/domains/mpc.md` | `python3 scripts/lodectx.py brief mpc` | Closed-loop Model Predictive Control (MPC), receding-horizon updates, and mesocycle step-cycles |
| `physiology` | `docs/domains/physiology.md` | `python3 scripts/lodectx.py brief physiology` | Dual-load modeling (Aerobic TSS vs Mechanical Strain), ANS costing, and tissue remodeling |
| `workouts` | `docs/domains/workouts.md` | `python3 scripts/lodectx.py brief workouts` | 80/20 workout library loading, sport schemas, zone calculations, and brick workout generation |
| `integrations` | `docs/domains/integrations.md` | `python3 scripts/lodectx.py brief integrations` | TrainingPeaks private REST API syncing, FIT file parsing, and 8020 workout scraping |
| `safety` | `docs/domains/safety.md` | `python3 scripts/lodectx.py brief safety` | Orthopedic guardrails, mechanical ACWR bounds, rolling strain caps, and injury protocols |
| `perf` | `docs/domains/perf.md` | `python3 scripts/lodectx.py brief perf` | Multiprocessing parallelism, CPU core scaling, timeout bounds, and solver benchmark budgets |

## 2. Engineering PRDs (`docs/specs/`)
- **`PRD-001`** (`optimizer`): Brick Transition Modeling and Same-Day Strength Stacking — Model same-day multi-discipline transitions and strength stacking within the HiGHS MILP constraint matrix.
- **`PRD-002`** (`optimizer`): Dual-Objective Hybrid Athlete MILP and MPC Optimization — Optimize concurrent triathlon and heavy barbell/dumbbell strength training via HiGHS MILP and 3-Tier closed-loop MPC.

## 3. Architectural Decisions Index (`ADR`)
- **`ADR-001`** (`general`): Adopt Progressive Disclosure & SQLite Lineage Architecture
- **`ADR-002`** (`optimizer`): Adopt HiGHS Mixed-Integer Linear Programming Solver via highspy
- **`ADR-003`** (`safety`): Enforce Single-Run Volume Ceiling at 32 Percent of Weekly Running Duration
- **`ADR-004`** (`physiology`): Decouple Aerobic TSS and Mechanical Strain with Asynchronous Tissue Remodeling
- **`ADR-005`** (`mpc`): Implement Three-Tier Closed-Loop Model Predictive Control with Daily Readiness Scaling
- **`ADR-006`** (`perf`): Adopt 15-Seed Parallel HiGHS Race with Per-Worker Timeout Limits [SUPERSEDED BY ADR-018 [SUPERSEDES: ADR-018 supersedes ADR-006]]
- **`ADR-007`** (`general`): Modularize Repository Layout, Data Assets, and Reference Artifacts
- **`ADR-008`** (`optimizer`): Adopt Dual-Objective Hybrid Athlete Optimization with Equipment Partitioning and Biomechanical Conflict Matrix
- **`ADR-009`** (`physiology`): Adopt Canonical 80/20 7-Zone Power System and Two-Point Critical Power Protocol
- **`ADR-010`** (`integrations`): Adopt Multi-Device Hardware Telemetry Rules and FIT Ingestion Invariants
- **`ADR-011`** (`physiology`): Integrate Closed-Loop Critical Power Retesting Engine with Dual Telemetry Triggers and Mesocycle Synchronization
- **`ADR-012`** (`mpc`): Lock Athlete Decision Record for Nov 2026 Re-Entry and Olympic Season
- **`ADR-013`** (`optimizer`): Enforce Three-Tier Lexicographic Constraint Hierarchy with Shared Tier-0 Safety Layer
- **`ADR-014`** (`workouts`): Adopt Single Validated Canonical Workout Library and YAML Athlete Profile
- **`ADR-015`** (`general`): Adopt src-layout triplan package with domain subpackages
- **`ADR-016`** (`general`): Scope the Startup Token Budget to the Charter's Read List
- **`ADR-017`** (`optimizer`): Unified WeekModel engine with modular constraint builders
- **`ADR-018`** (`optimizer`): Single deterministic solve with selection cuts, dominance pruning and symmetry breaking replaces the multi-seed race
- **`ADR-019`** (`mpc`): Carry-in state and in-model linearized running ACWR, CTL ramp and run-minutes rows
- **`ADR-020`** (`physiology`): Sport-specific quality q_i classifies hard and key sessions; lift+easy combos are compound candidates; canonical 80/20 is a hard engine rule
- **`ADR-021`** (`optimizer`): Re-entry weeks scale the structure minimums down when the full-week structure floor exceeds the TSS target
- **`ADR-022`** (`safety`): Absolute long-run cap in minutes for weeks with fewer than 4 runs
- **`ADR-023`** (`optimizer`): Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge
- **`ADR-024`** (`workouts`): Long-Course Library Rows, Profile-Level 48h Caps Bounded By The Charter
- **`ADR-025`** (`optimizer`): Race-block build weeks raise their TSS target to the structural floor
- **`ADR-028`** (`mpc`): Alternating long-run / long-ride keystone weeks in long-course race-block builds
- **`ADR-029`** (`workouts`): Include 80/20 Endurance Library Options in Default and Hybrid MILP Pools
- **`ADR-030`** (`safety`): Model Brick Run Leg Across Safety and Load-Dynamics Constraints
- **`ADR-031`** (`safety`): Three-week rolling window for receding-horizon progression guard
- **`ADR-032`** (`safety`): Support-run pool stratification and 90/105-min keystone run build-up
- **`ADR-033`** (`mpc`): Distance-aware long-ride keystone ladder in long-course builds
- **`ADR-034`** (`physiology`): Unified ANS cost, zone-power mechanical strain, set-intensity strength strain, 10-20 TSS/h strength and exact Banister PMC decay

## 4. Operational Heuristics Index by Domain (`HEUR`)
- **`workouts`:** `HEUR-003`, `HEUR-008`
- **`integrations`:** `HEUR-005`, `HEUR-006`, `HEUR-007`
- **`safety`:** `HEUR-002`, `HEUR-004`
- **`perf`:** `HEUR-001`
