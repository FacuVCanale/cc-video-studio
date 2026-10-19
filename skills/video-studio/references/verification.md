# Verification

Add these to Cinetic's gates (`lint-film`, `layout-audit`, `forensics`, `av-audit`, `probe`, `check-sync`,
and the critic rubric). A check that did not run does not count as passing: report it.

| What | Tool | Passes if |
|---|---|---|
| Frames from every changed shot | Cinetic's `sheet.py` / `grab.sh` | The director inspected them at full size beside the previous version |
| Determinism | `determinism.sh` | Concurrency 1 and 4: every frame is identical (PNG and decoded) |
| One-frame jumps | `jumps.py <mp4> --cues out/cues.json` | Only declared cuts remain |
| Faint tiles with content | `ghost_tiles.py <mp4>` | No frame contains blocks that differ from both neighbors while the neighbors match (or the director inspected the box and it is motion) |
| Stale rasterizer tiles | `vote_frames.py` over two or more full renders | Every disputed frame has a majority; frames without one are rerendered or chosen by eye |
| Transitions | the director, at real speed | Every cut and fast movement reads clearly and matches what the voice says |
| Cascades | the project's schedule test | Constant intervals (±1 frame) or monotonic acceleration; nothing exits before the transition; verified by deliberately breaking it |
| Take cutting | `silence_cut.py` / `tts_takes.py` | Every cut between lines landed in silence (`cut_in_silence`) |
| Voice edges | `voice_edges.py` (and `--measure peak`) | First and last 30 ms of every take < −45 dBFS RMS; no peak clicks |
| Voice against script | `voice_qc.py` | Every line matches the script; tone is consistent with the approved voice |
| Placement | `voice_mix.py` | No line overlaps another, is sped up, or runs past the end; ≥ 0.4 s breathing room at every cut |
| Voice preserved | hash or PCM comparison | The master voice bus is identical to the approved takes |
| Music | `music_edit.py`, then `beats.py` on the edited WAV | Climax at its cue, own ending in the final bar, remeasured beat deviation < 15 ms |
| Effects | `score_events.py` | Every declared event has its sound; none is over a word |
| Mix | `voice_mix.py`; `voice_qc.py --mix` | Voice at least 8 dB over music at 1–4 kHz; ≥ 95% of the script understood |
| Loudness | measurement after AAC | −14 LUFS; true peak ≤ −1 dBTP |
| Stream | `ffprobe` | The MP4 shown has video and audio of equal duration |
| Listening pass (optional, earshot) | `listen.py <mp4> --cues out/cues.json` | No expected cue is "without impact" without an explanation (word overlap, ducking); no clicks in the mix; read the table as style, not a target |
| Clicks by stem (optional, earshot) | `earshot.sh sfx check <stem>.wav` | Locate clicks in a stem and confirm they do not reach the mix |

## Listening with earshot (optional)
Before delivery, `listen.py` runs the mix through earshot in one process: listening transcription, PNG sheet, and
a table against launch films. If earshot is unavailable, report that the listening pass did not run and continue
with the remaining checks.
- **What to inspect in the sheet:** red cues (no impact) and violet cues (only a level peak). In a piece with voice,
  these are usually effects reduced because they land on a word: check them against the mix report before raising
  them.
- **Clicks:** run `sfx check` on the mix and every stem. A click in a stem that does not appear in the mix was
  masked; record it and move on. `voice_edges.py` still protects voice edges by measuring every take before mixing;
  earshot listens to the finished mix. Run both.
- **Known false positives:** over voice, `rising_glide` flags intonation; final "dead air" and "almost mono" in
  the table are style decisions until the user says otherwise. The table describes SaaS launch films: it is a
  reference, not a gate.

## Reading jumps
`jumps.py` flags frames where the image changes more than 3 times as much as in neighboring frames.
- **Expected:** a declared cut, a hit on the beat, a photo landing.
- **Errors:** a one-frame level-of-detail change, something appearing instantly at full opacity, a camera whip whose
  fastest point comes first.

A colored background reveals jumps that were not flagged over white: measure every background.

## Stale tiles and voting
A rasterizer with sporadic failures (Chromium in Remotion, especially on layers with blur or `will-change`)
occasionally draws a tile from another frame. The render is deterministic except for this, so each failure appears
in a different render and frame. `jumps.py` catches strong ones; `ghost_tiles.py` catches faint ones with content
(text or a panel from another moment at low opacity).
- **Voting:** render the master twice in full and have `vote_frames.py` compare them block by block, frame by frame.
  Where they differ, the render matching more of the others wins. Two renders have no majority: break the tie with
  a third render, or a previously voted backup with the same code in that segment (`--tiebreak`). If a tile appears
  in every render, add renders with a different concurrency; choose a frame with no majority by eye.
- **In Remotion, redo a segment without re-encoding the rest:** rerender the entire GOP containing the frames, cut
  the GOPs before and after it with `ffmpeg -f segment` (direct copy at keyframes), concatenate, and mux the audio
  again. The new segment may have its own failures: vote it again against the previous render. After joining, run
  the usual gates: decode the full video, confirm the same frame count, sync, and spec.

## Long renders
- **One heavy render at a time,** using a file lock shared by every agent (`out/.render.lock`). Two simultaneous
  renders compete for memory and the GPU, and the slower one kills the other.
- **`caffeinate -i`** every long render so the Mac does not sleep halfway through.
- **Launch the master detached from the session** (`nohup … &` with its log): the render continues if the agent or
  terminal closes.
- **A watchdog:** a separate process monitors the frame counter in the log and alerts if it stops advancing for
  more than a few minutes. A stalled render does not fail; it stays still.
- **If bundling fails with a webpack hash error** (`WasmHash`), the cache is corrupt: use
  `--bundle-cache=false` (`enableCaching: false` in `bundle()`).
- **Temporary bundles:** each `bundle()` without its own folder leaves ~170 MB in the system temp directory. Project
  scripts delete them on exit (`drop_bundle_on_exit.mjs`), and `clean_render_tmp.sh` removes leftovers from manual
  renders without touching anything while a render is running.

## Rules
- If cues change, regenerate the audio and subtitles and compare them with their predecessors before joining.
- Do not show a render the director has not inspected.
- If an agent stopped before verification, the director verifies it with these same tools.
