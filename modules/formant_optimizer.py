import numpy as np

def clipped_score(value, low, high):
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))

def adjacent_jump_rate(xs_arr, vals, threshold):
    vals = np.asarray(vals, dtype=float)
    xs_arr = np.asarray(xs_arr, dtype=float)
    valid = np.isfinite(vals)
    if np.sum(valid) < 3:
        return 1.0
    idx = np.where(valid)[0]
    jump_count = 0
    pair_count = 0
    for left, right in zip(idx[:-1], idx[1:]):
        if xs_arr[right] - xs_arr[left] > 0.055:
            continue
        pair_count += 1
        if abs(vals[right] - vals[left]) > threshold:
            jump_count += 1
    if pair_count == 0:
        return 1.0
    return jump_count / pair_count

def fragmentation_penalty(valid_mask):
    if len(valid_mask) == 0:
        return 1.0
    runs = []
    run_len = 0
    for flag in valid_mask:
        if flag:
            run_len += 1
        elif run_len:
            runs.append(run_len)
            run_len = 0
    if run_len:
        runs.append(run_len)
    if not runs:
        return 1.0
    short = sum(r for r in runs if r < 4)
    return short / max(1, sum(runs))

def score_config(snippets, formant_max_hz, window_length, pre_emphasis):
    total_frames = 0
    valid_frames = 0

    continuity_scores = []
    gap_scores = []
    range_scores = []
    fragmentation_scores = []
    edge_scores = []

    all_valid_f1 = []
    all_valid_f2 = []

    for snd_part in snippets:
        try:
            formant = snd_part.to_formant_burg(
                time_step=None,
                max_number_of_formants=5,
                maximum_formant=formant_max_hz,
                window_length=window_length,
                pre_emphasis_from=pre_emphasis
            )
        except Exception:
            continue

        xs = formant.xs()
        if len(xs) == 0:
            continue

        f1_vals = []
        f2_vals = []
        f3_vals = []
        for t in xs:
            f1_vals.append(formant.get_value_at_time(1, t))
            f2_vals.append(formant.get_value_at_time(2, t))
            f3_vals.append(formant.get_value_at_time(3, t))

        f1 = np.array(f1_vals)
        f2 = np.array(f2_vals)
        f3 = np.array(f3_vals)

        gap = f2 - f1
        valid_mask = (
            np.isfinite(f1) & np.isfinite(f2) &
            (f1 >= 90.0) & (f1 <= 1300.0) &
            (f2 >= 350.0) & (f2 <= min(float(formant_max_hz) * 0.96, 4200.0)) &
            (gap >= 120.0)
        )
        total_frames += len(xs)
        valid_frames += np.sum(valid_mask)

        if np.sum(valid_mask) < 4:
            continue

        vf1 = f1[valid_mask]
        vf2 = f2[valid_mask]
        vf3 = f3[valid_mask]

        all_valid_f1.extend(vf1.tolist())
        all_valid_f2.extend(vf2.tolist())

        valid_f1_series = np.where(valid_mask, f1, np.nan)
        valid_f2_series = np.where(valid_mask, f2, np.nan)
        f1_jump_threshold = max(150.0, 0.28 * np.nanmedian(vf1))
        f2_jump_threshold = max(260.0, 0.20 * np.nanmedian(vf2))
        f1_jump_rate = adjacent_jump_rate(xs, valid_f1_series, f1_jump_threshold)
        f2_jump_rate = adjacent_jump_rate(xs, valid_f2_series, f2_jump_threshold)
        continuity_scores.append(1.0 - min(1.0, 3.0 * (0.45 * f1_jump_rate + 0.55 * f2_jump_rate)))

        diff = vf2 - vf1
        diff_p10 = np.percentile(diff, 10)
        diff_cv = np.std(diff) / np.mean(diff) if np.mean(diff) > 0 else 1.0
        gap_score = 0.65 * clipped_score(diff_p10, 140.0, 450.0) + 0.35 * (1.0 - min(1.0, diff_cv / 0.85))
        gap_scores.append(gap_score)

        f1_p95 = np.percentile(vf1, 95)
        f2_p99 = np.percentile(vf2, 99)
        f1_range_penalty = clipped_score(f1_p95, 1050.0, 1450.0)
        f2_range_penalty = clipped_score(f2_p99, 3300.0, 4300.0)
        range_scores.append(1.0 - min(1.0, 0.55 * f1_range_penalty + 0.45 * f2_range_penalty))

        fragmentation_scores.append(1.0 - fragmentation_penalty(valid_mask))

        near_edge_f2 = np.sum(vf2 > 0.93 * formant_max_hz) / len(vf2)
        vf3_clean = vf3[np.isfinite(vf3)]
        near_edge_f3 = np.sum(vf3_clean > 0.93 * formant_max_hz) / len(vf3_clean) if len(vf3_clean) > 0 else 0.0
        edge_scores.append(1.0 - min(1.0, 0.7 * near_edge_f2 + 0.3 * near_edge_f3))

    if total_frames == 0:
        return 0.0, 0.0, np.nan, np.nan

    valid_rate = valid_frames / total_frames
    coverage_score = min(1.0, valid_rate / 0.82)
    continuity_score = np.mean(continuity_scores) if continuity_scores else 0.0
    gap_score = np.mean(gap_scores) if gap_scores else 0.0
    range_score = np.mean(range_scores) if range_scores else 0.0
    fragmentation_score = np.mean(fragmentation_scores) if fragmentation_scores else 0.0
    edge_score = np.mean(edge_scores) if edge_scores else 0.0

    quality_score = (
        0.22 * coverage_score +
        0.30 * continuity_score +
        0.22 * gap_score +
        0.12 * range_score +
        0.08 * fragmentation_score +
        0.06 * edge_score
    )

    if valid_rate < 0.25:
        quality_score *= 0.55

    # Prefer a lower ceiling when the observed F1/F2 quality is effectively tied.
    quality_score *= (1.0 - max(0.0, formant_max_hz - 5000.0) / 30000.0)

    median_f1 = np.median(all_valid_f1) if all_valid_f1 else np.nan
    median_f2 = np.median(all_valid_f2) if all_valid_f2 else np.nan

    return quality_score, valid_rate, median_f1, median_f2
