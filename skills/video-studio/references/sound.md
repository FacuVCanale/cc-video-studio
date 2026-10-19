# Sound: music, effects, and mixing

This replaces Cinetic's synthesized soundtrack **only if** the user prefers it after listening to both options
over the same cut. Until then, prepare both. Sound is never considered verified until the user listens to it:
measurements prevent broken work from reaching them; they do not prove that it sounds good.

## Music
- **Song first, then grid.** The user chooses it: a licensed track from a library (always take the terms from
  its official page) or a generated track they approve (with prior cost and cap). If there is a reference they
  like, analyze its tempo, band balance, and voice placement.
- **Generated music does not honor frame-exact durations** and may carry too much sub-bass energy: always measure it.
- **`beats.py`** reports tempo, beats, bars, and key. Verify drift on the kick drum (< 150 Hz): on the full mix, the
  tracker may lock onto the offbeat.
- **Grid.** Cinetic requires each beat to span an integer number of frames at 60 fps (120 BPM → 30 f). With a real
  song, fit the film to its grid: section cuts land on bars, and document anything that does not fit.
- **`music_edit.py`** edits the song to the film's duration, cutting only on bars:
  - the song's strongest moment lands on the climax or logo cue;
  - the song's own ending lands on the film's final bar;
  - never pad with silence (music cut before its ending is immediately audible);
  - if a scene is added, repeat bars at that point and keep everything after it on its beats.

## Effects
- **Real recordings before raw oscillators.** Use CC0 libraries or approved generated effects, with two or three
  files per family and slight gain and pan variation between repetitions. Align each effect by its measured peak to
  the event frame.
- **Density: Cinetic decides.** Use effects only on discrete state changes, around 5 every 10 s, where the music is
  sparser. If one instance of an action class makes a sound, all its instances do, at the same level. Continuous
  motion and most cuts do not get their own sound.
- **`score_events.py`** places a file on every event declared in `cues.json` and fails if any event lacks sound.
- **Intentional silence:** a hard cut before the logo is worth more than another effect.
- No effect over a word: keep ≥ 80 ms away, or reduce it by −10 dB.

## Effects made with code: earshot (optional)
[earshot](https://github.com/Leonxlnx/earshot) provides a bank of 201 effects stored as code (whooshes, impacts,
clicks, ticks, pops, tuned notes) that render in seconds, without models or a network connection. Cinetic uses it
for its kits. Run it through `scripts/earshot.sh` (pinned to a commit, with no global installation). If it exits
with code 3, it is unavailable: continue with recordings.
- **One effect on the frame:** `earshot.sh sfx render whoosh_fast/crisp -o /ABS/w.wav --dur 0.8 --apex 0.32` puts
  the whoosh's strongest moment within < 5 ms of `apex`; `--pitch E5` tunes notes and tonal impacts to the music's
  key. Every render passes the audit (clicks, cut tails, clipping, the rising-pitch "whip").
- **A full kit:** `earshot.sh kit init --preset calm -o /ABS/audio/kit.json` (`calm` for pieces with text or voice),
  then `earshot.sh kit render /ABS/audio/kit.json --out /ABS/audio/kit`. Takes for each role become a family in the
  `score_events.py` map, which aligns them by their measured peak.
- **When not to use it:** if the piece calls for recorded sound (real objects, foley), use recordings; the bank is
  synthesis and processing of CC0 recordings, not a collection of recordings. It also adds little under continuous
  voice: if nearly every event lands on a word and is reduced, a better effect still cannot be heard. Make room
  first (pauses, J-cuts), then choose the sound.
- **Choose by ear:** `earshot.sh ab a.wav b.wav --query "a soft wooden tick"` compares two candidates and
  `earshot.sh describe x.wav` describes what one sounds like. These are clues; the user chooses by listening.

## Mix
- **Duck the music by frequency band while the voice speaks:**
  - 7 dB in the mids (250 Hz–5 kHz), 2 dB in the lows, and 3 dB in the highs;
  - 120 ms attack and 600 ms release;
  - do not raise it again during pauses shorter than 1.5 s.

  Reducing the full band by 10 dB makes the music disappear.
- **Voice over music:** around 8 dB above the music in the speech band (1–4 kHz) while speaking. In the full mix,
  transcription must understand at least 95% of the script.
- Apply a −6 dB low shelf below 120 Hz if the music has excess sub-bass.
- **Master:** −14 LUFS integrated and true peak ≤ −1 dBTP, measured after AAC encoding (Cinetic: ≤ −1.5 dBTP in
  the WAV).
- Mux the mix into the video without re-encoding the image, and verify sync (Cinetic's `check-sync.py`).

## Paid services
- Every paid call goes through `fal_run.py`: it estimates the cost first, stops at the cap in
  `audio/fal/budget.json` (`{"cap_usd", "approved_by", "date"}`, written by the payer), and keeps a spending ledger.
  The key comes from the environment (`FAL_KEY` or `FAL_KEY_CMD`) and is never printed.
- When spending approaches the cap, report how much was spent and how much remains.
