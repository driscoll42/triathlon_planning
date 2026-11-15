---
id: HEUR-XXX
type: heuristic
domain: <domain>
title: "<Subsystem + Specific Trap or Quirk>"
status: ACTIVE # ACTIVE | SUPERSEDED
created_at: YYYY-MM-DD
supersedes: []
validated_by: []
one_liner: "<The exact 'Never do X; always do Y because Z' rule (<160 chars)>"
---

# `HEUR-XXX`: <Title>

- **Target Subsystem / Path:** Exact file, library, config, or runtime component.
- **Symptom / Failure Mode:** Exact error message, silent bug, race condition, or performance cliff observed.
- **Root Physics / Mechanism:** Why the compiler, kernel, database, framework, or protocol behaves this way.
- **Permanent Operational Rule:** Exact code pattern, flag, or guardrail that prevents this failure.
- **Verification Check:** `1-line CLI command or test invocation to verify compliance`.
