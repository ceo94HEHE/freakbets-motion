# UI morph

A 14-second loop at 1440×1440: one shape that becomes a button, a loader, a check, a dynamic island, a music player, a volume slider, a toggle, tabs, a chart, a ⌘K palette and a toast, then turns back into the button. There are 7 bars at 120 BPM, and something happens on every beat.

## Pipeline

```sh
python3 beats.py song.mp3           # beat grid, downbeat, 7-bar window, key → beats.json
node render.mjs beats               # one frame per beat + contact sheet → out/qa/
node render.mjs beats --at 0.4      # same, 0.4 beat later (the landed states)
node render.mjs full                # 4 subframes per 60 fps frame → out/sub/
python3 audio.py --song song.mp3    # UI sounds on their cues + the song window → out/audio.wav
./build.sh                          # tmix motion blur + mux → out/ui-morph.mp4
```

### Other pieces

`rtp-channel/` is a 1:1 (1080×1080) loop for the RTP 🇮🇹 Telegram channel. It uses the same engine, moving through Telegram's UI into a slot feature moment and back to a join button. The symbols are original, and "18+ · Gioca responsabilmente" stays on screen. Every tool takes `--dir`:

```sh
node render.mjs beats --dir rtp-channel
node render.mjs full --dir rtp-channel
python3 audio.py --dir rtp-channel
./build.sh rtp-channel               # → rtp-channel/out/rtp-channel.mp4
```

Without a song, `audio.py` writes only the UI sounds, and everything runs on an exact 120 BPM grid. `track.json` (`{"title", "artist", "duration"}`) sets the credit shown in the island and the player.

Needs Node 22 with Playwright's Chromium, Python 3 with numpy (Pillow for the diagnostic plot), and ffmpeg. `python3 beats.py --selftest` checks the beat tracker on a synthetic drum loop.

## How it works

- **`seek(t)` is the whole animation.** Every style is computed from `t` alone, with no CSS transitions, timers or state carried between frames. Opened directly in a browser, `index.html` plays in real time. Click to pause; `?t=4.5` or `?beat=9` freezes it at a moment.
- **Springs are closed-form step responses.** A value that changes target several times is the sum of one spring per change, with cyclic keys, so the value at the end of the loop equals the value at its start.
- **Liquid edges.** The tab indicator, the toggle knob and the loader arc have two edges on different springs. The leading edge is stiff and the trailing edge soft, so the shape stretches ahead, then catches up.
- **Drags are direct manipulation.** While the cursor is held, the value comes from the cursor's position. On release it springs back from wherever it was, with the velocity it had.
- **UI sounds.** Every UI sound is synthesized in `audio.py`. Its peak is measured, and it's placed so that peak lands on the cue. The beat grid is fitted to the song's waveform peaks too.
- **The loop is seamless.** Frame 840 is frame 0. The cursor path is a cyclic quintic spline, so its position and speed match across the seam, and the audio seam is cross-faded.

Geist is used under the SIL Open Font License (`fonts/Geist-OFL.txt`). Icon paths are adapted from Lucide (ISC).
