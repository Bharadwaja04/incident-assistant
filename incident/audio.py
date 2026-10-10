"""Stage B of the stutter-handling front end: magnitude (amplitude) filtering before speech to text.

Frames whose loudness is below a threshold are treated as noise and faded out. The threshold is not a fixed number:
it is worked out from the recording itself (the quiet frames show the noise floor), so a factory hum and a quiet room
both work. A short hangover keeps the soft starts and ends of words. A high-pass filter removes machine rumble.
"""
from __future__ import annotations


def high_pass(x, rate=16000, cutoff=80.0):
    import numpy as np
    a = np.exp(-2 * np.pi * cutoff / rate)
    y = np.empty_like(x)
    prev_x = prev_y = 0.0
    for i, v in enumerate(x):                # first-order filter; fine for a few seconds of speech
        prev_y = a * (prev_y + v - prev_x)
        prev_x = v
        y[i] = prev_y
    return y


def magnitude_filter(x, rate=16000, frame_ms=20, factor=2.5, hang_ms=120):
    """Returns (filtered samples, stats). threshold = factor x noise floor (10th percentile of frame loudness)."""
    import numpy as np
    n = max(1, int(rate * frame_ms / 1000))
    nfr = len(x) // n
    stats = {"threshold": 0.0, "noise_floor": 0.0, "gated_pct": 0.0}
    if nfr < 5:
        return x, stats
    fr = x[: nfr * n].reshape(nfr, n)
    rms = np.sqrt((fr ** 2).mean(axis=1))
    floor = float(np.percentile(rms, 10))
    peak = float(np.percentile(rms, 95))
    thr = max(floor * factor, 0.004)
    if peak < thr * 1.5:                      # no clear speech above the noise: leave it alone, Whisper's own checks decide
        stats.update(threshold=thr, noise_floor=floor)
        return x, stats
    keep = rms >= thr
    hang = max(1, int(hang_ms / frame_ms))
    k = keep.copy()
    for i in np.flatnonzero(keep):            # hangover: keep a little before and after each loud frame
        k[max(0, i - hang): i + hang + 1] = True
    gain = np.where(k, 1.0, 0.0).astype(np.float32)
    gain = np.convolve(gain, np.ones(3) / 3, mode="same")          # soft edges, no clicks
    y = x.copy()
    y[: nfr * n] = (fr * gain[:, None]).reshape(-1)
    stats.update(threshold=round(thr, 4), noise_floor=round(floor, 4), gated_pct=round(float((~k).mean()) * 100, 1))
    return y, stats
