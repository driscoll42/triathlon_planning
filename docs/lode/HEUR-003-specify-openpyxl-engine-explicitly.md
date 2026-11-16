---
id: HEUR-003
type: heuristic
domain: workouts
title: "Specify Openpyxl Engine Explicitly in pandas read_excel"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
one_liner: "Always pass engine='openpyxl' to pd.read_excel to prevent xlrd failures on .xlsx workout databases."
---

# `HEUR-003`: Specify Openpyxl Engine Explicitly in pandas read_excel

- **Target Subsystem / Path:** `util.py:load_workouts()`, `clean_file.py`.
- **Symptom / Failure Mode:** `xlrd.biffh.XLRDError: Excel xlsx file; not supported` during workout library loading.
- **Root Physics / Mechanism:** Modern versions of `xlrd` (>=2.0.0) removed support for `.xlsx` files and only support legacy `.xls`. If pandas cannot infer or defaults to `xlrd`, reading `Run.xlsx` or `Bike.xlsx` crashes immediately.
- **Permanent Operational Rule:** Every call to `pd.read_excel()` on `.xlsx` files must explicitly specify `engine='openpyxl'`.
- **Verification Check:** `python -c "import pandas as pd; pd.read_excel('data/Run.xlsx', engine='openpyxl')"`.
