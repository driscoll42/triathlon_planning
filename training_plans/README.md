# 80/20 Triathlon Official Training Plans Dataset

This directory contains the official training plans extracted directly from ***80/20 Triathlon: Triple Your Fitness and Train Smarter with the Multi-Sport 80/20 System*** by Matt Fitzgerald & David Warden.

---

## Summary Statistics

- **Total Plans Extracted**: 17 training plans
  - **Sprint Distance**: Level 0, Level 1, Level 2, Level 3 (12 to 19 weeks)
  - **Olympic Distance**: Level 0, Level 1, Level 2, Level 3 (16 to 19 weeks)
  - **Half Ironman Distance (70.3)**: Level 0, Level 1, Level 2, Level 3 (18 to 21 weeks)
  - **Ironman Distance (140.6)**: Level 0, Level 1, Level 2, Level 3 (21 to 23 weeks)
  - **Maintenance**: 12 weeks
- **Total Weekly Plan Rows**: 966 rows
- **Total Scheduled Workouts**: 2,698 individual workouts
- **Unique Workout Codes Covered**: 485 codes mapped to descriptions and TSS

---

## File Directory

### Data Files (CSV)
1. [`plans.csv`](plans.csv):
   - **Schema**: `race, level, week, phase, sport, mon, tue, wed, thu, fri, sat, sun, weekly_volume, total_duration, pct_low, pct_high`
   - **Best for**: Preserving the tabular weekly grid by sport and day of the week, with weekly volume targets and 80/20 low/high intensity balance.

2. [`plans_daily.csv`](plans_daily.csv):
   - **Schema**: `race, level, week, phase, day, sport, workout_code, description`
   - **Best for**: Programmatic lookups, pandas filtering, database insertion, calendar generation (`.ics`), and TrainingPeaks sync.

3. [`brick_workouts.csv`](brick_workouts.csv):
   - **Schema**: `Code, Duration, Zone 1, Zone 2, Zone 3, Zone 4, Zone 5, Description`
   - **Best for**: Reference for Brick workouts (`BR1` through `BR6`).

### Human-Readable Reference Files (Markdown)
1. [`sprint.md`](sprint.md): Complete Sprint Distance plans (Levels 0, 1, 2, 3) + Workout glossary.
2. [`olympic.md`](olympic.md): Complete Olympic Distance plans (Levels 0, 1, 2, 3) + Workout glossary.
3. [`half_ironman.md`](half_ironman.md): Complete Half Ironman plans (Levels 0, 1, 2, 3) + Workout glossary.
4. [`ironman.md`](ironman.md): Complete Ironman plans (Levels 0, 1, 2, 3) + Workout glossary.
5. [`maintenance.md`](maintenance.md): Complete 12-week Maintenance plan + Workout glossary.

---

## Quick Usage Examples (Python / Pandas)

### 1. Filter Workouts for a Specific Plan
```python
import pandas as pd

df = pd.read_csv('training_plans/plans_daily.csv')

# Get all workouts for Olympic Distance, Level 1, Week 4
week_4 = df[(df['race'] == 'Olympic') & (df['level'] == 'Level 1') & (df['week'] == 4)]
print(week_4[['day', 'sport', 'workout_code', 'description']])
```

### 2. Join with Your Workout Library
```python
import pandas as pd

plans = pd.read_csv('training_plans/plans_daily.csv')
bike_lib = pd.read_excel('Bike.xlsx')

# Join bike workouts with their target TSS and Zone splits
bike_plan = plans[plans['sport'] == 'Cycle'].merge(
    bike_lib, left_on='workout_code', right_on='Code', how='left'
)
print(bike_plan[['week', 'day', 'workout_code', 'TSS', 'Duration', 'Zone 1', 'Zone 2', 'Zone 3']])
```
