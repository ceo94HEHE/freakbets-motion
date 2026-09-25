#!/usr/bin/env python3
"""Beat grid for the song, with numpy only.

    python3 beats.py ../song.mp3        → beats.json (+ out/beats.png, a diagnostic plot)
    python3 beats.py --selftest         checks itself on a synthetic drum loop

1. Onset envelope: log-magnitude spectral flux.
2. Tempo: autocorrelation of the envelope with a prior around 120 BPM, then a least-squares
   fit of a straight beat grid to the local onset peaks (so the period is sub-millisecond).
3. Downbeat: of the four bar phases, the one where the snare/clap lands on 2 and 4 and the
   harmony changes on 1.
4. Window: the 7-bar stretch starting on a downbeat with the most energy and the smoothest seam
   (the bar after the window should sound like its first bar, because that is what the loop
   jumps back to).
5. Key: chroma against Krumhansl profiles, so the success chime can be tuned to the song.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 22050
HOP = 128          # onset envelope hop (5.8 ms)
NFFT = 1024
C_HOP = 1024       # chroma STFT
C_NFFT = 4096
BARS = 7


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def stft_mag(x, nfft=NFFT, hop=HOP):
    win = np.hanning(nfft)
    n = 1 + (len(x) - nfft) // hop
    out = np.empty((n, nfft // 2 + 1))
    for a in range(0, n, 2048):                               # in blocks, to bound memory
        idx = np.arange(nfft)[None, :] + hop * np.arange(a, min(n, a + 2048))[:, None]
        out[a:a + len(idx)] = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    return out                                                # frames × bins


def onset_envelope(mag):
    logm = np.log1p(1000 * mag / (mag.max() + 1e-12))
    flux = np.maximum(0, np.diff(logm, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    # subtract a local mean (≈0.4 s) so sustained loudness doesn't count as onsets
    k = int(0.4 * SR / HOP) | 1
    local = np.convolve(flux, np.ones(k) / k, mode="same")
    env = np.maximum(0, flux - local)
    g = np.exp(-0.5 * (np.arange(-6, 7) / 2.0) ** 2)          # σ ≈ 12 ms
    env = np.convolve(env, g / g.sum(), mode="same")
    return env / (env.max() + 1e-12)


def band_energy(mag, lo, hi, nfft=NFFT):
    f = np.fft.rfftfreq(nfft, 1 / SR)
    sel = (f >= lo) & (f < hi)
    return (mag[:, sel] ** 2).sum(axis=1)


def chroma(mag, nfft=C_NFFT):
    f = np.fft.rfftfreq(nfft, 1 / SR)
    sel = (f > 60) & (f < 4000)
    pc = np.round(12 * np.log2(f[sel] / 440.0)).astype(int) % 12     # 0 = A
    C = np.zeros((mag.shape[0], 12))
    for c in range(12):
        C[:, c] = (mag[:, sel][:, pc == c] ** 2).sum(axis=1)
    return C


def tempo(env, fps, lo=70, hi=180, prior=120.0, width=0.3):
    """Autocorrelation summed over the first four multiples of each candidate beat lag (a true
    beat has peaks at 1, 2, 3 and 4 beats), weighted by a log-normal prior around 120 BPM."""
    e = env - env.mean()
    n = len(e)
    spec = np.fft.rfft(e, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac /= ac[0] + 1e-12
    lags = np.arange(n)
    cands = np.arange(lo, hi, 0.05)
    L = 60 * fps / cands
    score = sum(np.interp(m * L, lags, ac) / m ** 0.3 for m in (1, 2, 3, 4))
    score *= np.exp(-0.5 * (np.log2(cands / prior) / width) ** 2)
    return float(cands[int(np.argmax(score))])


def fit_line(ks, ts, ws):
    A = np.stack([np.ones_like(ks, dtype=float), ks.astype(float)], axis=1) * ws[:, None]
    return np.linalg.lstsq(A, ts * ws, rcond=None)[0]


def fit_grid(env, fps, bpm, x):
    """Straight beat grid: best comb phase, then a least-squares line through the actual
    waveform peak of every beat (UI sounds are placed by their peaks, so the grid is too)."""
    P = 60 * fps / bpm
    n = len(env)
    frame = np.arange(n)
    phases = np.linspace(0, P, 240, endpoint=False)
    comb = [np.interp(np.arange(ph, n - 1, P), frame, env).sum() for ph in phases]
    grid = np.arange(phases[int(np.argmax(comb))], n - 1, P)
    # envelope peak near each grid point (frame centre sits ~NFFT/4 after the onset)
    w = int(0.05 * fps)
    ks, ts, ws = [], [], []
    for k, g in enumerate(grid):
        a, b = int(max(0, g - w)), int(min(n, g + w + 1))
        i = a + int(np.argmax(env[a:b]))
        if env[i] > 0.1:
            ks.append(k); ts.append(i * HOP / SR + NFFT / 4 / SR); ws.append(env[i])
    ks, ts, ws = map(np.asarray, (ks, ts, ws))
    for _ in range(2):                                        # drop outliers, refit
        t0, Pf = fit_line(ks, ts, ws)
        keep = np.abs(ts - (t0 + Pf * ks)) < 0.025
        ks, ts, ws = ks[keep], ts[keep], ws[keep]
    # the waveform peak of each beat, from 10 ms before to 40 ms after the onset
    amp = np.sqrt(np.convolve(x * x, np.ones(44) / 44, mode="same"))   # 2 ms RMS
    peaks = []
    for k, t in zip(ks, ts):
        a, b = int((t - 0.010) * SR), int((t + 0.040) * SR)
        if a < 0 or b >= len(x):
            peaks.append(np.nan); continue
        peaks.append((a + int(np.argmax(amp[a:b]))) / SR)
    peaks = np.asarray(peaks)
    ok = np.isfinite(peaks)
    t0, Pf = fit_line(ks[ok], peaks[ok], ws[ok])
    resid = (peaks[ok] - (t0 + Pf * ks[ok])) * 1000
    return t0, Pf, float(np.sqrt(np.mean(resid ** 2)))


def beat_features(mag, cmag, t0, P, fps, count):
    times = t0 + P * np.arange(count)
    # clamp rather than drop, so feature i always belongs to beat i
    frames = np.clip(np.round((times - NFFT / 4 / SR) * fps).astype(int), 0, None)
    keep = frames < mag.shape[0] - int(P * fps)
    times, frames = times[keep], frames[keep]
    w = max(1, int(0.05 * fps))
    low = band_energy(mag, 30, 150)
    snare = band_energy(mag, 1500, 6000)
    C = chroma(cmag)
    cfps = SR / C_HOP
    cf = np.clip(np.round(times * cfps).astype(int), 0, len(C) - 1)
    span = max(1, int(P * cfps))
    feats = {
        "low": np.array([low[max(0, f - 1): f + w].mean() for f in frames]),
        "snare": np.array([snare[max(0, f - 1): f + w].mean() for f in frames]),
        "chroma": np.array([C[c: c + span].mean(axis=0) for c in cf]),
        "rms": np.array([(mag[f: f + int(P * fps)] ** 2).sum() for f in frames]),
    }
    return times, feats


def downbeat_phase(feats):
    s = feats["snare"] / (feats["snare"].mean() + 1e-12)
    c = feats["chroma"] / (np.linalg.norm(feats["chroma"], axis=1, keepdims=True) + 1e-12)
    change = np.concatenate([[0], 1 - (c[1:] * c[:-1]).sum(axis=1)])
    scores = []
    for p in range(4):
        idx = np.arange(len(s))
        on1 = (idx - p) % 4 == 0
        backbeat = s[(idx - p) % 2 == 1].mean() - s[(idx - p) % 2 == 0].mean()
        harm = change[on1].mean() - change[~on1].mean()
        scores.append(backbeat / (np.std(s) + 1e-9) + 2.0 * harm / (np.std(change) + 1e-9))
    return int(np.argmax(scores)), scores


KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NAMES = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]


def key_of(chroma_vec):
    best = (-2, 0, "major")
    for r in range(12):
        for prof, mode in ((KS_MAJOR, "major"), (KS_MINOR, "minor")):
            cc = np.corrcoef(np.roll(prof, r), chroma_vec)[0, 1]
            if cc > best[0]:
                best = (cc, r, mode)
    cc, r, mode = best
    hz = 440.0 * 2 ** (r / 12)
    while hz < 660:
        hz *= 2
    while hz >= 1320:
        hz /= 2
    return f"{NAMES[r]} {mode}", hz, cc


def choose_window(feats, phase, beats_per_bar=4):
    nbeats = len(feats["rms"])
    L = BARS * beats_per_bar
    rms = feats["rms"] / (feats["rms"].max() + 1e-12)
    c = feats["chroma"] / (np.linalg.norm(feats["chroma"], axis=1, keepdims=True) + 1e-12)
    best = None
    for s in range(phase, nbeats - L - beats_per_bar, beats_per_bar):
        energy = rms[s: s + L].mean()
        steady = 1 - rms[s: s + L].std() / (energy + 1e-9)
        seam_c = (c[s: s + 4] * c[s + L: s + L + 4]).sum(axis=1).mean()          # bar after ≈ first bar
        seam_e = 1 - abs(rms[s + L: s + L + 4].mean() - rms[s: s + 4].mean()) / (energy + 1e-9)
        score = energy + 0.5 * steady + 0.6 * seam_c + 0.4 * seam_e
        if best is None or score > best[0]:
            best = (score, s, dict(energy=energy, steady=steady, seam_chroma=seam_c, seam_energy=seam_e))
    return best


def analyse(x, plot=None):
    mag = stft_mag(x)
    cmag = stft_mag(x, C_NFFT, C_HOP)
    env = onset_envelope(mag)
    fps = SR / HOP
    bpm0 = tempo(env, fps)
    t0, P, rms_ms = fit_grid(env, fps, bpm0, x)
    t0 = t0 - P * np.floor(t0 / P)                            # first beat of the song
    count = int((len(x) / SR - t0) / P)
    times, feats = beat_features(mag, cmag, t0, P, fps, count)
    phase, pscores = downbeat_phase(feats)
    score, s, parts = choose_window(feats, phase)
    start = t0 + s * P
    L = BARS * 4
    kname, khz, kcc = key_of(feats["chroma"][s: s + L].sum(axis=0))
    out = {
        "bpm": round(60 / P, 4), "period": P, "tempo_estimate": bpm0, "first_beat": t0, "downbeat_phase": phase,
        "start": start, "end": start + L * P, "start_beat_index": int(s),
        "grid_rms_ms": round(rms_ms, 2), "phase_scores": [round(float(v), 3) for v in pscores],
        "window": {k: round(float(v), 3) for k, v in parts.items()},
        "key": kname, "key_confidence": round(float(kcc), 3), "chime_hz": round(khz, 2),
        "duration": len(x) / SR,
    }
    if plot:
        draw(env, fps, t0, P, phase, start, start + L * P, plot)
    return out


def draw(env, fps, t0, P, phase, a, b, path):
    from PIL import Image, ImageDraw
    W, H = 2400, 360
    t_lo, t_hi = max(0, a - 2 * P), b + 4 * P
    im = Image.new("RGB", (W, H), (250, 249, 246))
    d = ImageDraw.Draw(im)
    def X(t):
        return (t - t_lo) / (t_hi - t_lo) * W
    k = np.ceil((t_lo - t0) / P)
    while t0 + k * P < t_hi:
        t = t0 + k * P
        down = int(k) % 4 == phase
        d.line([(X(t), 0), (X(t), H)], fill=(255, 107, 0) if down else (210, 206, 198), width=3 if down else 1)
        k += 1
    d.rectangle([X(a), 0, X(b), 8], fill=(14, 14, 14))
    i0, i1 = int(t_lo * fps), int(min(len(env) - 1, t_hi * fps))
    pts = [(X(i * HOP / SR + NFFT / 4 / SR), H - 20 - env[i] * (H - 60)) for i in range(i0, i1)]
    d.line(pts, fill=(14, 14, 14), width=2)
    im.save(path)


def selftest():
    rng = np.random.default_rng(3)
    bpm, off = 123.0, 0.713
    P = 60 / bpm
    dur = 60.0
    x = np.zeros(int(dur * SR))
    t = np.arange(int(0.25 * SR)) / SR
    kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-t / 0.03)) * t) * np.exp(-t / 0.12)
    clap = rng.standard_normal(len(t)) * np.exp(-t / 0.05)
    hat = rng.standard_normal(int(0.04 * SR)) * np.exp(-np.arange(int(0.04 * SR)) / SR / 0.008)
    chords = [220.0, 174.61, 261.63, 196.0]
    k = 0
    while off + k * P < dur - 1:
        i = int((off + k * P) * SR)
        x[i:i + len(kick)] += kick
        if k % 2 == 1:
            x[i:i + len(clap)] += 0.5 * clap
        j = int((off + (k + 0.5) * P) * SR)
        x[j:j + len(hat)] += 0.15 * hat
        if k % 4 == 0:                                      # a chord change on every downbeat
            f = chords[(k // 4) % 4]
            n = int(4 * P * SR)
            tt = np.arange(n) / SR
            pad = sum(np.sin(2 * np.pi * f * m * tt) / m for m in (1, 1.25, 1.5, 2))
            x[i:i + n] += 0.08 * pad[: len(x) - i]
        k += 1
    x += 0.003 * rng.standard_normal(len(x))
    r = analyse(x)
    ok_bpm = abs(r["bpm"] - bpm) < 0.05
    err = ((r["start"] - off + 2 * P) % (4 * P)) - 2 * P               # seconds from a true downbeat
    ok_phase = abs(err) < 0.012
    print(f"downbeat error {err * 1000:+.1f} ms (the peak sits just after the onset)")
    print(json.dumps({k: r[k] for k in ("bpm", "start", "grid_rms_ms", "downbeat_phase", "phase_scores")}, indent=1))
    print(f"tempo {'ok' if ok_bpm else 'WRONG'} ({r['bpm']} vs {bpm}); downbeat {'ok' if ok_phase else 'WRONG'}")
    return ok_bpm and ok_phase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song", nargs="?", type=Path)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--start", type=float, help="force the window start (seconds, snapped to the nearest downbeat)")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    x = decode(args.song)
    (HERE / "out").mkdir(exist_ok=True)
    r = analyse(x, plot=HERE / "out" / "beats.png")
    if args.start is not None:
        bar = 4 * r["period"]
        first_down = r["first_beat"] + r["downbeat_phase"] * r["period"]
        r["start"] = first_down + round((args.start - first_down) / bar) * bar
        r["end"] = r["start"] + BARS * bar
    r["song"] = str(args.song)
    (HERE / "beats.json").write_text(json.dumps(r, indent=2))
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
