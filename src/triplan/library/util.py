from triplan.model.classes import triVariables as triv


def load_workouts(include_strength=True, library_path=None):
    """Weekend / weekday candidate frames (legacy Title-Case schema) from the canonical library.

    Source: data/workout_library.csv, validated by Pandera + Pydantic (TASK-011). The former
    file arguments (Run/Bike/Swim xlsx, brick and strength CSVs) were removed: those files were
    duplicate sources. Hand-coded engine test libraries (provenance hand_coded_*) are excluded,
    as before. Sports with a 0% target split in triVariables are skipped.
    """
    from triplan.library.loader import legacy_frame, load_library, select

    sports = [s for s, pct in (("Run", triv.run_tgt_percent), ("Bike", triv.bike_tgt_percent),
                               ("Swim", triv.swim_tgt_percent)) if pct > 0] + ["Brick"]
    if include_strength:
        sports.append("Strength")
    lib = select(load_library(library_path), provenance=["8020_library", "agent_authored"], sports=sports)
    df = legacy_frame(lib)

    wkend_wts_df = df[(df['Duration'] <= triv.max_time_weekend) & (df['TSS'] <= triv.max_tss_weekend)]
    wkday_wts_df = df[(df['Duration'] <= triv.max_time_weekday) & (df['TSS'] <= triv.max_tss_weekday)]
    return wkend_wts_df.reset_index(drop=True), wkday_wts_df.reset_index(drop=True)


def calculate_plan_score(wkt, verbose=False, target_tss=None):
    """Legacy plan quality score. REPORTING METRIC ONLY (TASK-020): it no longer drives any optimisation
    (the random generator and multi-seed race were retired); the MILP objective is the engine's own goals. target_tss defaults to triVariables.target_tss;
    pass it explicitly instead of mutating the class global (TASK-013)."""
    target_tss = triv.target_tss if target_tss is None else target_tss
    def calculate_sport_scores(wkt, exer):
        exer.dur_percent = exer.dur / wkt.duration
        spt_dur_tgt_score = 1.0 - abs(1.0 - exer.dur_percent / exer.tgt_percent)
        if verbose: print(exer.name, 'Dur % TGT Score:', round(spt_dur_tgt_score, 2), exer.name, 'Dur %:',
                          round(exer.dur_percent, 2), 'Total', exer.name, 'Dur:', exer.dur)

        spt_dur_tgt_score *= triv.rbs_dur_split_weight * exer.tgt_percent

        exer.tss_percent = exer.tss / wkt.total_tss
        spt_tss_tgt_score = 1.0 - abs(1.0 - exer.tss_percent / exer.tgt_percent)
        if verbose: print(exer.name, 'TSS % TGT Score:', round(spt_tss_tgt_score, 2), exer.name, 'TSS %:',
                          round(exer.tss_percent, 2), 'Total', exer.name, 'TSS:', exer.tss)
        spt_tss_tgt_score *= triv.rbs_tss_split_weight * exer.tgt_percent

        exer.high_dur_percent = (exer.high_dur / exer.dur) if exer.dur > 0 else 0.0
        exer.low_dur_percent = (exer.low_dur / exer.dur) if exer.dur > 0 else 0.0

        spt_high_tgt_score = 1.0 - abs(1.0 - exer.high_dur_percent / triv.high_tgt)
        spt_low_tgt_score = 1.0 - abs(1.0 - exer.low_dur_percent / triv.low_tgt)

        if verbose: print(exer.name, 'High TGT Score:', round(spt_high_tgt_score, 2), exer.name, 'High %:',
                          round(exer.high_dur_percent, 2))
        if verbose: print(exer.name, 'Low TGT Score:', round(spt_low_tgt_score, 2), exer.name, 'Low %:',
                          +    round(exer.low_dur_percent, 2))
        dur_split_tgt_score = (
                                      spt_high_tgt_score + spt_low_tgt_score) / 2 * exer.tgt_percent * triv.rbs_dur_tgt_split_weight

        exer.zone_3_percent = (exer.zone_3 / exer.high_dur) if exer.high_dur > 0 else 0.0
        spt_z3_tgt_score = 1.0 - abs(1.0 - exer.zone_3_percent / triv.zone_3_tgt)
        exer.zone_4_percent = (exer.zone_4 / exer.high_dur) if exer.high_dur > 0 else 0.0
        spt_z4_tgt_score = 1.0 - abs(1.0 - exer.zone_4_percent / triv.zone_4_tgt)
        exer.zone_5_percent = (exer.zone_5 / exer.high_dur) if exer.high_dur > 0 else 0.0
        spt_z5_tgt_score = 1.0 - abs(1.0 - exer.zone_5_percent / triv.zone_5_tgt)

        if verbose: print(exer.name, 'Zone 3 TGT Score:', round(spt_z3_tgt_score, 2), exer.name, 'Zone 3 %:',
                          round(exer.zone_3_percent, 2))
        if verbose: print(exer.name, 'Zone 4 TGT Score:', round(spt_z4_tgt_score, 2), exer.name, 'Zone 4 %:',
                          +    round(exer.zone_4_percent, 2))
        if verbose: print(exer.name, 'Zone 5 TGT Score:', round(spt_z5_tgt_score, 2), exer.name, 'Zone 5 %:',
                          +    round(exer.zone_5_percent, 2))
        high_split_tgt_score = (
                                       spt_z3_tgt_score + spt_z4_tgt_score + spt_z5_tgt_score) / 3 * exer.tgt_percent * triv.high_zone_split_weight

        wkt.tss_tgt_score += spt_tss_tgt_score
        wkt.dur_tgt_score += spt_dur_tgt_score
        wkt.dur_split_tgt_score += dur_split_tgt_score
        wkt.high_split_tgt_score += high_split_tgt_score

    if triv.tot_tss_weight > 0.0:
        wkt.tss_score = 1.0 - abs(1.0 - wkt.total_tss / target_tss)
        if verbose: print('TSS Score:', round(wkt.tss_score, 2), 'TSS:', wkt.total_tss)
        wkt.tss_score *= triv.tot_tss_weight

    # ----------------------------------------------
    if triv.tgt_weight > 0.0:
        wkt.high_dur_percent = wkt.high_dur / wkt.duration
        wkt.low_dur_percent = wkt.low_dur / wkt.duration

        wkt.high_tgt_score = 1.0 - abs(1.0 - wkt.high_dur_percent / triv.high_tgt)
        wkt.low_tgt_score = 1.0 - abs(1.0 - wkt.low_dur_percent / triv.low_tgt)
        if verbose: print('High TGT Score:', round(wkt.high_tgt_score, 2), 'High %:', round(wkt.high_dur_percent, 2))
        if verbose: print('Low TGT Score:', round(wkt.low_tgt_score, 2), 'Low %:', round(wkt.low_dur_percent, 2))

        wkt.tgt_score = (wkt.high_tgt_score + wkt.low_tgt_score) * triv.tgt_weight / 2

    # ----------------------------------------------
    if triv.run_tgt_percent > 0.0:
        calculate_sport_scores(wkt, wkt.run)

    if triv.bike_tgt_percent > 0.0:
        calculate_sport_scores(wkt, wkt.bike)

    if triv.swim_tgt_percent > 0.0:
        calculate_sport_scores(wkt, wkt.swim)

    # ----------------------------------------------
    wkt.score = round(
            wkt.tss_score + wkt.tgt_score + wkt.dur_tgt_score + wkt.tss_tgt_score + wkt.dur_split_tgt_score + wkt.high_split_tgt_score,
            4) * 100
