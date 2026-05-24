import numpy as np


def detect_pitch_anomaly_points(p_xs, p_freqs, bounds=None, start=None, end=None):
    """Return point-level F0 anomalies as (time, frequency) tuples."""
    if p_xs is None or p_freqs is None:
        return []

    xs = np.asarray(p_xs, dtype=float)
    freqs = np.asarray(p_freqs, dtype=float)
    if len(xs) == 0 or len(freqs) == 0:
        return []

    n = min(len(xs), len(freqs))
    xs = xs[:n]
    freqs = freqs[:n]

    finite = np.isfinite(xs) & np.isfinite(freqs)
    if start is not None:
        finite &= xs >= start
    if end is not None:
        finite &= xs <= end
    xs = xs[finite]
    freqs = freqs[finite]
    if len(xs) == 0:
        return []

    if not bounds:
        if start is None:
            start = float(np.min(xs))
        if end is None:
            end = float(np.max(xs))
        bounds = [(start, end)]

    anomalies = {}
    for b_s, b_e in bounds:
        in_bound = (xs >= b_s) & (xs <= b_e)
        b_xs = xs[in_bound]
        b_freqs = freqs[in_bound]
        active = (b_freqs > 0) & np.isfinite(b_freqs)
        active_xs = b_xs[active]
        active_freqs = b_freqs[active]

        if len(active_freqs) < 4:
            continue

        median = float(np.median(active_freqs))
        if median <= 0:
            continue

        high_ratio = active_freqs / median
        low_ratio = median / active_freqs
        abs_delta = np.abs(active_freqs - median)

        # A point is only considered a hard anomaly when it looks like a
        # doubled/halved pitch track or an isolated noise estimate, not merely
        # a normal tonal slope inside the syllable.
        candidate_mask = (
            (abs_delta >= 45.0)
            & ((high_ratio >= 1.70) | (low_ratio >= 1.70))
        )

        if not np.any(candidate_mask):
            continue

        # Very broad "candidate" regions usually mean the whole syllable has a
        # different pitch level, not scattered repairable points.
        candidate_count = int(np.sum(candidate_mask))
        if candidate_count > max(6, int(np.ceil(len(active_freqs) * 0.45))):
            continue

        for t, f in zip(active_xs[candidate_mask], active_freqs[candidate_mask]):
            key = round(float(t), 6)
            anomalies[key] = (float(t), float(f))

    return [anomalies[k] for k in sorted(anomalies)]
