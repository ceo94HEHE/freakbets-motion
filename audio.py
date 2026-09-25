#!/usr/bin/env python3
"""UI sounds and the music bed for the loop.

    python3 audio.py                     UI sounds only (draft)
    python3 audio.py --song song.mp3     UI sounds over the song, cut on the grid in beats.json
    python3 audio.py --dir rtp-channel   another piece (reads/writes that folder)

Reads out/timeline.json (written by render.mjs) and writes out/audio.wav.
Every UI sound is synthesised here. Its peak is measured and it is placed so that peak lands
exactly on its cue. Anything still ringing at the loop end wraps round to the start, so the
file loops without a click.
"""
import argparse
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 48000
ROOT = Path(__file__).resolve().parent
HERE = ROOT
OUT = HERE / "out"
rng = np.random.default_rng(7)


# ── building blocks ──────────────────────────────────────────────────────────
def decay(n, tau):
    return np.exp(-np.arange(n) / (tau * SR))


def tone(freq, dur, tau, attack=0.0008):
    n = int(dur * SR)
    t = np.arange(n) / SR
    a = np.minimum(1, t / attack) if attack else 1
    return np.sin(2 * np.pi * freq * t) * decay(n, tau) * a


def sweep(f0, f1, dur, tau, glide=0.025):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t / glide)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * decay(n, tau)


def noise(dur, tau):
    n = int(dur * SR)
    return rng.standard_normal(n) * decay(n, tau)


def band(x, lo, hi):
    """Soft band-pass in the frequency domain (4th-order-ish skirts)."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    w = 1 / (1 + (lo / np.maximum(f, 1)) ** 4) / (1 + (f / hi) ** 4)
    return np.fft.irfft(spec * w, len(x))


def layer(*parts):
    n = max(len(p) for p, _ in parts)
    out = np.zeros(n)
    for p, at in parts:
        i = int(at * SR)
        out[i:i + len(p)] += p[: n - i]
    return out


def norm(x, peak):
    return x / (np.max(np.abs(x)) + 1e-12) * peak


def declick(x, ms=0.3):
    n = max(1, int(ms * SR / 1000))
    x[:n] *= np.linspace(0, 1, n)
    x[-n:] *= np.linspace(1, 0, n)
    return x


# ── the sounds ───────────────────────────────────────────────────────────────
def click():
    return layer((band(noise(0.03, 0.0022), 1800, 7000), 0), (tone(2100, 0.03, 0.006) * 0.35, 0), (tone(190, 0.06, 0.012) * 0.5, 0))


def build_sounds(root_hz):
    s = {}
    s["click"] = norm(click(), 0.50)
    s["tab"] = norm(layer((band(noise(0.025, 0.0018), 2200, 8000), 0), (tone(2500, 0.025, 0.005) * 0.3, 0), (tone(210, 0.05, 0.01) * 0.35, 0)), 0.36)
    s["grab"] = norm(layer((band(noise(0.03, 0.003), 900, 4000), 0), (tone(1200, 0.04, 0.008) * 0.3, 0), (tone(160, 0.07, 0.016) * 0.55, 0)), 0.42)
    s["drop"] = norm(layer((band(noise(0.02, 0.0015), 2500, 9000), 0), (tone(2600, 0.02, 0.004) * 0.3, 0)), 0.30)
    s["thud"] = norm(layer((sweep(150, 52, 0.22, 0.05), 0), (band(noise(0.02, 0.002), 600, 3000) * 0.35, 0)), 0.62)
    latch = band(noise(0.015, 0.0012), 3000, 10000)
    s["toggle"] = norm(layer((band(noise(0.025, 0.0018), 2000, 8000), 0), (tone(3000, 0.02, 0.004) * 0.4, 0),
                             (tone(260, 0.045, 0.01) * 0.4, 0), (latch * 0.45, 0.021)), 0.46)
    s["tick"] = norm(layer((tone(3200, 0.02, 0.003), 0), (band(noise(0.01, 0.001), 3000, 10000) * 0.5, 0)), 0.20)
    key_up = band(noise(0.012, 0.0012), 2500, 9000) * 0.28
    s["key"] = norm(layer((band(noise(0.05, 0.006), 700, 5000), 0), (tone(330, 0.06, 0.012) * 0.6, 0),
                          (tone(1600, 0.03, 0.005) * 0.2, 0), (key_up, 0.048)), 0.44)
    s["enter"] = norm(layer((band(noise(0.07, 0.009), 400, 4000), 0), (tone(220, 0.09, 0.02) * 0.7, 0),
                            (key_up * 1.2, 0.062)), 0.54)
    # success: two soft partials a fifth apart, the second a hair later, over a click
    chime = layer((tone(root_hz, 0.6, 0.20, 0.002) * 0.6 + tone(root_hz * 2, 0.6, 0.08, 0.002) * 0.12, 0),
                  (tone(root_hz * 1.5, 0.6, 0.16, 0.002) * 0.45, 0.055))
    s["success"] = norm(layer((chime, 0), (click() * 0.25, 0)), 0.46)

    # slot and chat sounds (rtp-channel)
    def bell(f, dur=0.7, tau=0.22, amp=1.0):
        return (tone(f, dur, tau, 0.002) + 0.35 * tone(f * 2.76, dur, tau * 0.45, 0.002) + 0.15 * tone(f * 5.4, dur, tau * 0.2, 0.002)) * amp
    s["tap"] = norm(layer((band(noise(0.02, 0.0015), 2500, 9000), 0), (tone(1900, 0.02, 0.004) * 0.3, 0)), 0.32)
    s["pulse"] = norm(layer((tone(root_hz / 2, 0.25, 0.07, 0.004), 0), (tone(root_hz, 0.2, 0.04, 0.004) * 0.3, 0)), 0.26)
    s["type"] = norm(layer(*[(band(noise(0.012, 0.0012), 3000, 9000) * (0.7 + 0.3 * (i % 2)), i * 0.055) for i in range(5)]), 0.18)
    pop = np.sin(2 * np.pi * np.cumsum(np.linspace(500, 1300, int(0.03 * SR))) / SR) * decay(int(0.03 * SR), 0.008)
    s["pop"] = norm(layer((pop, 0), (band(noise(0.01, 0.001), 2000, 8000) * 0.3, 0)), 0.38)
    s["reel"] = norm(layer((sweep(190, 80, 0.12, 0.03), 0), (band(noise(0.02, 0.002), 800, 4000) * 0.5, 0)), 0.5)
    for i, ratio in enumerate((1.0, 1.25, 1.5), 1):
        s[f"scatter{i}"] = norm(bell(root_hz * ratio), 0.42)
    s["tick"] = norm(layer((tone(2600, 0.015, 0.003), 0), (band(noise(0.008, 0.001), 3000, 10000) * 0.4, 0)), 0.2)
    s["bonus"] = norm(layer((bell(root_hz) + bell(root_hz * 1.25) + bell(root_hz * 1.5) + bell(root_hz * 2, amp=0.6), 0),
                            (band(noise(0.25, 0.08), 4000, 12000) * 0.25, 0.01)), 0.58)
    s["drop"] = norm(layer((band(noise(0.06, 0.012), 150, 1200), 0), (sweep(120, 60, 0.08, 0.02) * 0.6, 0)), 0.34)
    s["burst"] = norm(layer((band(noise(0.05, 0.01), 2000, 9000), 0), (bell(root_hz * 3, 0.3, 0.06, 0.4), 0)), 0.44)
    s["orb"] = norm(layer((bell(root_hz * 2, 0.5, 0.12), 0), (bell(root_hz * 3, 0.4, 0.08, 0.5), 0.04), (band(noise(0.2, 0.05), 5000, 12000) * 0.2, 0)), 0.4)
    s["mega"] = norm(layer((sweep(130, 38, 0.6, 0.18), 0), (bell(root_hz) + bell(root_hz * 1.5) + bell(root_hz * 2), 0.005),
                           (band(noise(0.3, 0.07), 300, 6000) * 0.4, 0)), 0.66)
    return {k: declick(v.copy()) for k, v in s.items()}


def peak_index(x):
    return int(np.argmax(np.abs(x)))


# ── music bed ────────────────────────────────────────────────────────────────
def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def music_bed(song, start, length, n_out, xfade=0.02):
    """song[start : start+length], resampled to n_out samples, with the audio right after the
    loop end cross-faded into the start so the seam is continuous."""
    a = int(round(start * SR))
    b = a + int(round(length * SR))
    x = int(xfade * SR)
    seg = song[a:b + x].copy()
    body, tail = seg[: b - a], seg[b - a:]
    ramp = np.linspace(0, 1, len(tail))[:, None]
    body[: len(tail)] = body[: len(tail)] * ramp + tail * (1 - ramp)
    # resample to exactly the video length (a fraction of a percent, inaudible)
    src = np.linspace(0, len(body) - 1, n_out)
    return np.stack([np.interp(src, np.arange(len(body)), body[:, c]) for c in range(2)], axis=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", type=Path)
    ap.add_argument("--sfx-gain", type=float, default=1.0)
    ap.add_argument("--dir", default=".")
    args = ap.parse_args()
    global HERE, OUT
    HERE = ROOT / args.dir
    OUT = HERE / "out"

    tl = json.loads((OUT / "timeline.json").read_text())
    n = int(round(tl["frames"] / tl["fps"] * SR))
    beats = json.loads((HERE / "beats.json").read_text()) if (HERE / "beats.json").exists() else {}
    sounds = build_sounds(beats.get("chime_hz", 660.0 if args.dir != "." else 880.0))

    ui = np.zeros(n)
    report = []
    for cue in tl["SFX"]:
        s = sounds[cue["type"]]
        p = peak_index(s)
        at = int(round(cue["t"] * SR)) - p          # peak on the cue
        idx = (at + np.arange(len(s))) % n          # wrap past the loop end
        np.add.at(ui, idx, s)
        report.append((cue["beat"], cue["type"], p / SR * 1000))

    mix = np.stack([ui, ui], axis=1) * args.sfx_gain
    if args.song:
        song = decode(args.song)
        bed = music_bed(song, beats["start"], tl["T"], n)
        bed *= 10 ** (-1 / 20) / (np.max(np.abs(bed)) + 1e-12)
        mix = bed * 0.82 + mix * 0.55

    peak = np.max(np.abs(mix))
    if peak > 10 ** (-1 / 20):
        mix *= 10 ** (-1 / 20) / peak
    pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2")
    OUT.mkdir(exist_ok=True)
    with wave.open(str(OUT / "audio.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    for beat, kind, pk in report:
        print(f"beat {beat:>5}: {kind:<8} peak at {pk:5.2f} ms into the sound")
    print(f"wrote {(OUT / 'audio.wav').relative_to(ROOT)} · {n / SR:.3f}s")


if __name__ == "__main__":
    main()
