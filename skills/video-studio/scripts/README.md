# Audio and verification workflow

These scripts complement Cinetic. They read its `out/cues.json` and WAV/MP4 files; they do not load a page or call `window.seek`. Use absolute paths in arguments. Each Python script declares its PEP 723 dependencies and runs with `uv run`, without a virtual environment or `requirements.txt`. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and FFmpeg/ffprobe on the `PATH`. `determinism.sh` also requires Node and the project's Remotion dependencies.

## Inputs

`cues.json` is produced by Cinetic's `export-cues.ts`:

```json
{
  "fps": 60, "bpm": 120, "total": 540,
  "acts": {"opening": {"from": 0, "dur": 240}},
  "cue": {"climax": 360},
  "events": [{"f": 210, "kind": "whoosh", "weight": 1, "pan": -0.5, "id": "a:entrance"}]
}
```

Frames are absolute and `f` may be fractional. `score_events.py` uses a JSON map by family, with WAV files recorded at 48 kHz:

```json
{"whoosh": {"files": ["sfx/whoosh-*.wav"], "gain": 0.2, "pan": 0}}
```

Paths in `files` are resolved from the map's directory; each family requires two or three variants. Word marks for `score_events.py --words` are a JSON array of `{ "start": 1.2, "end": 1.5, "word": "hello" }`, in absolute seconds, or the `{"fps": ..., "words": [...]}` object exported by `voice_mix.py`. The script for voice tools is a JSON array of lines with `id`, `text`, optional `tts`, `anchor` (a `cue` name), and optional `take`. Per-line WAV files are named `<id>.wav`.

`music_edit.py --inserts` accepts JSON in the form `[ {"frame": 240, "bars": 2} ]`. The film duration must land on the bar grid; otherwise the script fails so the timeline can be adjusted without cutting the song within a bar.

## Workflow

| Step | Cinetic | video-studio | Result |
|---|---|---|---|
| 0-3 | Brief, concept, timeline, and `export-cues.ts` | Define the song, script, and effect families | `out/cues.json`, script, map |
| 5a | Compare Cinetic's synthesized score | `beats.py`, `music_edit.py` | Analysis JSON and edited music WAV |
| 5b | Voice as the master clock | `tts_takes.py` (cuts with `silence_cut.py`), `voice_edges.py`, `voice_qc.py` | Takes, per-line WAV files, and QC |
| 5c | Initial placement, without effects | `voice_mix.py` | Voice only, SRT, and per-word marks |
| 5d | Cues and voice marks | `score_events.py` | WAV stem and placement JSON |
| 5e | Synchronize final audio | `voice_mix.py` with the effects stem | Final mix and subtitles |
| 7 | `lint-film`, `forensics`, `av-audit`, `check-sync` | `jumps.py`, `ghost_tiles.py`, `vote_frames.py`, `determinism.sh`, `voice_qc.py --mix` | Reports, voted master, and visual/audio inspection |

The user chooses between real or edited music and Cinetic's score by listening to both against the same cut. Measurements do not replace that listening pass.

## Main commands

```bash
uv run /ABS/skills/video-studio/scripts/beats.py /ABS/audio/song.wav --output /ABS/audio/song.beats.json
uv run /ABS/skills/video-studio/scripts/music_edit.py /ABS/audio/song.wav --cues /ABS/out/cues.json --climax-cue climax --analysis /ABS/audio/song.beats.json --output /ABS/audio/music.wav
uv run /ABS/skills/video-studio/scripts/tts_takes.py generate --script /ABS/audio/voice/script.json --output-dir /ABS/audio/voice/takes --backend kokoro
uv run /ABS/skills/video-studio/scripts/voice_qc.py --script /ABS/audio/voice/script.json --audio-dir /ABS/audio/voice/lines --output /ABS/audio/voice/qc.json --model tiny
uv run /ABS/skills/video-studio/scripts/voice_mix.py --script /ABS/audio/voice/script.json --cues /ABS/out/cues.json --audio-dir /ABS/audio/voice/lines --music /ABS/audio/music.wav --output /ABS/audio/mix.wav --voice-only /ABS/audio/voice-only.wav --placement /ABS/audio/placement.json --srt /ABS/audio/subtitles.srt --words /ABS/audio/words.json
uv run /ABS/skills/video-studio/scripts/score_events.py --cues /ABS/out/cues.json --map /ABS/audio/events.json --words /ABS/audio/words.json --output /ABS/audio/stems/effects.wav
uv run /ABS/skills/video-studio/scripts/voice_mix.py --script /ABS/audio/voice/script.json --cues /ABS/out/cues.json --audio-dir /ABS/audio/voice/lines --music /ABS/audio/music.wav --effects /ABS/audio/stems/effects.wav --output /ABS/audio/final-mix.wav --voice-only /ABS/audio/final-voice-only.wav --placement /ABS/audio/final-placement.json --srt /ABS/audio/final-subtitles.srt --words /ABS/audio/final-words.json
uv run /ABS/skills/video-studio/scripts/silence_cut.py --audio /ABS/audio/voice/take.wav --words /ABS/audio/voice/take.words.json --lines /ABS/audio/voice/take.lines.json --output-dir /ABS/audio/voice/lines
uv run /ABS/skills/video-studio/scripts/voice_edges.py /ABS/audio/voice/lines/*.wav
uv run /ABS/skills/video-studio/scripts/voice_edges.py /ABS/audio/voice/lines --measure peak --window-ms 20 --threshold-dbfs -30
uv run /ABS/skills/video-studio/scripts/jumps.py /ABS/out/film.mp4 --cues /ABS/out/cues.json --out /ABS/out/jumps.json --sheet /ABS/out/jumps.jpg
uv run /ABS/skills/video-studio/scripts/ghost_tiles.py /ABS/out/film.mp4 --out /ABS/out/ghost.json --csv /ABS/out/ghost.csv
uv run /ABS/skills/video-studio/scripts/vote_frames.py /ABS/out/film-a.mp4 /ABS/out/film-b.mp4 --tiebreak /ABS/out/backup.mp4 --plan /ABS/out/vote.json --out-video /ABS/out/voted-film.mp4
bash /ABS/skills/video-studio/scripts/clean_render_tmp.sh --older-than 6 --dry-run
bash /ABS/skills/video-studio/scripts/determinism.sh --project /ABS/project --entry /ABS/project/src/index.ts --composition Film --total-frames 540 --out /ABS/out/determinism
```

Every adjustable option is documented in each script's `--help`. `silence_cut.py` cuts a take into lines at the longest silence gap between the last word of one line and the first word of the next, measured by energy, because TTS alignment can be offset; it accepts word times (`--words`) or ElevenLabs character alignment (`--alignment`) and returns code 1 if any cut did not land in silence. `tts_takes.py` uses the same function. `ghost_tiles.py` flags frames containing blocks that differ from both neighbors while the neighbors match (faint tiles that `jumps.py` does not see) and reports each bounding box. `vote_frames.py` compares two or more complete renders by blocks, chooses the majority for each disputed frame (`--tiebreak` only votes), and builds the voted video without audio when passed `--out-video`. `clean_render_tmp.sh` deletes old Remotion bundles from the temporary directory when no render is running; in the project, `drop_bundle_on_exit.mjs` deletes its own bundle on exit. `jumps.py` returns code 1 when it detects jumps; the report labels jumps near a declared cut for human review. `determinism.sh` renders PNG sequences with concurrency 1 and 4 and compares hashes and pixels. Wait for other renders to finish before using it.

## Optional earshot integration

[earshot](https://github.com/Leonxlnx/earshot) (MIT) is an optional dependency pinned to commit `925ebe5` (v0.3.0). Its code is neither copied nor installed globally:

- `earshot.sh <command>` resolves it in this order: `EARSHOT_PYTHON` (a dedicated virtual environment; the same variable Cinetic uses; `none` disables it) or `uvx --from git+https://github.com/Leonxlnx/earshot@<sha>`. If neither is available, it exits with code 3 and the rest of the workflow continues unchanged. `EARSHOT_REF` changes the commit for testing a new version; update the pinned commit in `earshot.sh` and `listen.py` together (verified by `tests/test_earshot.py`).
- `listen.py` declares earshot in PEP 723 at the same commit and runs measurements, transcription, sheet, and table in one process (models load once, about 1 minute for 2 minutes of film on CPU).
- `sfx`, `kit`, and `measure` do not load models. `timeline`, `sheet`, `critique`, `describe`, `match`, and `ab` download two models on first use (about 1.1 GB, stored in `~/.cache/earshot` or `EARSHOT_HOME`).

```bash
uv run /ABS/skills/video-studio/scripts/listen.py /ABS/out/film.mp4 --cues /ABS/out/cues.json --out-dir /ABS/out/qa/listen
bash /ABS/skills/video-studio/scripts/earshot.sh sfx check /ABS/out/stems/sfx.wav
bash /ABS/skills/video-studio/scripts/earshot.sh sfx render whoosh_fast/crisp -o /ABS/audio/sfx/w.wav --dur 0.8 --apex 0.32
bash /ABS/skills/video-studio/scripts/earshot.sh kit init --preset calm -o /ABS/audio/kit.json
bash /ABS/skills/video-studio/scripts/earshot.sh kit render /ABS/audio/kit.json --out /ABS/audio/kit
```

## Voice and fal

`tts_takes.py` groups 2-4 lines per take. Kokoro mode runs locally and uses faster-whisper word times to cut lines; its first run must download the models. fal mode builds a JSONL batch of `{id, endpoint, input, out}` for `fal_run.py`. Run `--estimate` first. For paid calls, the person paying writes `/ABS/audio/fal/budget.json` with `cap_usd`, `approved_by`, and `date`. The script reads the key from `FAL_KEY` or `FAL_KEY_CMD`, stores the ledger in `audio/fal/spending.jsonl`, and stores responses in `audio/fal/logs/<id>.json`. A reservation without an output file blocks retries until the request is reconciled in fal.

The included prices for Eleven v3, Sound Effects v2, MiniMax Music 2.6, and ElevenLabs Music are estimates checked on October 14, 2026 against the [official fal pricing pages](https://fal.ai/pricing). For ElevenLabs Music, declare `music_length_ms` or `composition_plan.sections[].duration_ms` before estimating; open-ended duration is rejected. Check the price before running a batch. Tests make no network calls. `voice_qc.py` uses faster-whisper locally; select `--model tiny` for a lightweight test when the machine is free. The real Kokoro test is optional.

`voice_mix.py` preserves the approved voice bus without changing its gain and adjusts only the background to reach the master target. If it cannot meet loudness and peak limits under that condition, it fails and asks for the takes or background to be leveled. It also temporarily encodes and decodes AAC to measure the result: the WAV must remain below -1.5 dBTP and the AAC at -14 LUFS within the CLI tolerance, with true peak at or below -1 dBTP. The final MP4 is joined and verified with Cinetic; this script does not re-encode the image.

## Verification

Run tests from the repository with `uv run --with pytest --with numpy --with scipy --with soundfile --with Pillow --with tqdm --with pyloudnorm pytest -q tests`. Tests in `test_earshot.py` that require earshot run when it is available (through `EARSHOT_PYTHON` or the uv cache; they do not access the network) and skip otherwise. Fixtures synthesize their own audio/video and do not store large binaries. In addition to the tests, verify the final MP4's AAC audio with `ffprobe` and Cinetic's `check-sync.py`, inspect frames at full size, and listen to the finished mix.
