---
name: video-studio
description: "Production of code-driven videos (corporate, product, launch, demos) using the Cinetic method: adds the sound workflow (music selected and edited to the film, effects per event, voice in takes with transcript checks, multiband mixing), voice and image verification, and lessons from real productions. Use alongside the cinetic skill whenever making, reviewing, or finishing a video with Remotion or HyperFrames, or when asked to improve its sound, voice, or finish."
---

# video-studio

This skill does not replace **cinetic**: it completes it. Cinetic governs the image (concept, device, grid,
motion system, composition, transitions, blur rendering, and critic cycle). When this skill and Cinetic
disagree, Cinetic wins, except where this skill explicitly declares a replacement (sound, voice, and content
lessons).

The user commissions the film and makes the final calls on the material, music, voice, and budget. The director is
the agent leading the production and inspects every render before the user sees it.

Read `references/lessons.md` before starting: these are corrections already made once in real productions.

## What it adds to Cinetic

| Area | What it adds | File |
|---|---|---|
| Content | Where the words and images come from; the product from its code; licensed data and photos | `references/product-and-data.md` |
| Sound | Music selected and edited to bars, effects recorded per event, multiband mixing | `references/sound.md` |
| Voice | Multiline takes, transcript and tone checks, placement without cuts | `references/voice.md` |
| Verification | Image and audio checks added to Cinetic's gates | `references/verification.md` |
| Lessons | What real productions rejected or requested, turned into rules | `references/lessons.md` |

## Where it fits into the Cinetic workflow

- **Step 0 (intake):** before `BRIEF.md`, gather the inputs in `references/product-and-data.md`. The user's
  requests go into the brief verbatim.
- **Step 1 (concept):** on-screen text and voice come from the user's material, not the treatment. If there is
  an outside reference, borrow its craft, never its content.
- **Step 3 (timeline):** if the piece has narration, the voice is the master clock. No sentence is cut off, there
  is ≥ 0.4 s of breathing room between sentences, and the next shot may start 4–8 frames early (J-cut).
- **Step 5 (sound):** before synthesizing Cinetic's soundtrack, propose the workflow in `references/sound.md`
  (real or generated music, plus recorded effects). The user makes the final choice by listening to both
  options over the same cut.
- **Step 5 (sound), effects on the frame:** if a missing effect must be created, or a kit is needed for a piece
  without voice, earshot renders it from code with its peak on the frame (`references/sound.md`, "earshot").
- **Step 7 (review):** add the checks in `references/verification.md` to Cinetic's checks. Before delivery, run
  the earshot listening pass (`listen.py`) if installed; otherwise, report that it did not run.
- **Before showing:** inspect the frames yourself at full size, beside the previous version. A green gate is not
  approval.

## Tools

The scripts in `scripts/` read the cues exported by Cinetic (`out/cues.json`) and the rendered MP4. They do not
depend on the engine. Each explains its options with `--help`, and all parameters are passed through the CLI.

| Need | Script |
|---|---|
| Tempo, beats, bars, and key of a song | `beats.py` |
| Edit the song to the film's duration, cutting on bars | `music_edit.py` |
| Place a recorded effect on each declared event | `score_events.py` |
| Voice takes (local Kokoro or ElevenLabs through fal) and line cutting | `tts_takes.py` |
| Cut a take into lines at real silence between words, never at the alignment midpoint | `silence_cut.py` |
| Transcribe each line and compare it with the script; consistent tone | `voice_qc.py` |
| Place the voice, duck music by frequency band, mix, and write subtitles | `voice_mix.py` |
| Paid calls with a spending cap and ledger | `fal_run.py` |
| Edges of each take below −45 dBFS RMS, or free of peak clicks (uncut voice) | `voice_edges.py` |
| One-frame jumps around each cue | `jumps.py` |
| Faint ghost tiles: content from another frame in a single frame | `ghost_tiles.py` |
| Vote frame by frame across full renders and build the master by majority | `vote_frames.py` |
| Determinism: identical frames at concurrency 1 and 4 | `determinism.sh` |
| Delete temporary Remotion bundles (on exit, and leftovers) | `drop_bundle_on_exit.mjs`, `clean_render_tmp.sh` |
| Listen to the mix or film before delivery: events, cue sync and cuts, PNG sheet, table (earshot, optional) | `listen.py` |
| Run any earshot command (bank effects on the frame, kits, click audit, A/B), pinned to a commit | `earshot.sh` |

## Paid services

Every paid call (voice, music, or generated effects) is presented in advance with a plan and cost, and runs with
a cap written by the user in the project (`audio/fal/budget.json`). Free, reversible work is done and shown.
