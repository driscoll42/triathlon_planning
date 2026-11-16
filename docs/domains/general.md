# Domain Lode: `general`

## 1. Scope & Responsibility
Cross-cutting system architecture, engineering standards, Python runtime conventions, virtual environment management, the Lode Context framework, and foundational literature grounding.

## 2. Key Modules & Entrypoints
- [`.agents/rules/operating_charter.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/rules/operating_charter.md), [`.agents/AGENTS.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/AGENTS.md), [`.agents/GEMINI.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/GEMINI.md): Agent operating charters and progressive disclosure protocol.
- [`src/triplan/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/): Core platform package (`model/`, `library/`, `solver/`, `physiology/`, `control/`, `io/`, `perf/`; ADR-015) and root [`pyproject.toml`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/pyproject.toml).
- [`config/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/config/): Environment configuration (`config/.env.example`, `config/trainingpeak.env`).
- [`scripts/lodectx.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/lodectx.py): Zero-dependency SQLite WAL lineage engine and context CLI.
- [`docs/lode.json`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/lode.json): Project domain topology and token budget configuration.
- [`references/Closed-Loop Triathlon Training Optimizer.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/references/Closed-Loop%20Triathlon%20Training%20Optimizer.md): Physiological & mathematical specification.
- [`requirements.txt`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/requirements.txt): Thin pointer to `-e .[test,garmin]`; dependencies are defined only in `pyproject.toml` (ADR-015).

## 3. Core Architectural Rules & Standards

### A. Runtime Environment & Cross-Platform Conventions
- All code must run cleanly under Python 3.10+ on Windows and Linux without platform-specific path assumptions (use `pathlib.Path`).
- Maintain strict typing (`typing.Dict`, `List`, `Tuple`, `Optional`) and frozen dataclass schemas across model boundaries.
- Repository Layout & Clean Architecture (`ADR-007`): Source code in `src/`, environment secrets in `config/`, data assets in `data/`, reference texts in `references/`, operational tools in `scripts/`, output plans in `training_plans/`, and documentation in `docs/`. Never pollute the project root.

### B. Progressive Disclosure Context Engine
- Keep the startup core ([`.agents/GEMINI.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/GEMINI.md), [`docs/STATE_SUMMARY.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/STATE_SUMMARY.md), [`docs/LODE_MAP.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/LODE_MAP.md), [`docs/INVARIANTS.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/INVARIANTS.md), [`docs/TASKS.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/TASKS.md)) strictly under **7,500 estimated tokens**.
- Detailed domain lodes in `docs/domains/` are read on-demand during briefings (`python scripts/lodectx.py brief <domain>`).

### C. Foundational Literature Grounding
The platform synthesizes empirical exercise physiology and endurance coaching methodologies:
- **Joe Friel (*The Triathlete's Training Bible*):** 5-2 rule, abilities taxonomy (AE, TE, ME, AC, SS, MF), periodization phases (AA $\to$ MT $\to$ MS $\to$ SM), and missed-workout absorption rules.
- **Jim Vance (*Triathlon 2.0*):** Separate Bike PMC vs Run PMC, Vance Quadrant Analysis (Q1–Q4), cadence/power fade metrics, and running power pacing rules off the bike.
- **Patrick Hagerman (*Strength Training for Triathletes*):** Big-before-small, multi-joint compound movement ordering, heavy low-volume resistance protocols (85%–90% 1RM) paired with low-volume plyometrics for tendon stiffness.
- **Matt Fitzgerald (*80/20 Running*, *Triathlon Science*):** 80/20 polarized distribution, moderate-intensity rut avoidance, Interleukin-6 (IL-6) cytokine systemic inflammation from consecutive glycogen-depleting days.
- **Stephen Seiler:** 3-zone intensity demarcation, polarized training models, and post-exercise autonomic parasympathetic reactivation (RMSSD) threshold behavior at $LT1$.
- **Ed Maunder et al. (2021):** Physiological durability (fatigue resistance) and threshold degradation under prolonged mechanical work (>1,500–2,000 kJ).

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
