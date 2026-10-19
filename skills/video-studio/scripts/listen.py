# /// script
# requires-python = ">=3.10"
# dependencies = ["earshot @ git+https://github.com/Leonxlnx/earshot@925ebe56a1b755e5d354ab642bb8f344440cfa9c"]
# ///
"""Listen to a mix or film with earshot before delivery, in one process (models load once).

Writes to --out-dir:
  listen.json  the earshot listening transcript (timed, leveled, labeled events, sections, loudness,
               sync with cuts and cues), the comparison table against professional launch films, and objective
               measurements (LUFS, true peak, LRA, noise floor)
  listen.txt   the same information as readable text
  sheet.png    frames, events, spectrogram, loudness, and cues colored by sync (green: within one frame;
               amber: within 100 ms; red: no sound)

The measurements are reliable; event labels within a dense mix are clues, and the critique table describes a style
(SaaS launch films), not a target. None of this replaces the user listening to the mix.

usage: uv run listen.py /ABS/out/film.mp4 --cues /ABS/out/cues.json --out-dir /ABS/out/qa/listen
       uv run listen.py /ABS/audio/mix.wav --video /ABS/out/film.mp4 --out-dir /ABS/out/qa/listen --start 40 --end 55
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="mix WAV or film MP4")
    ap.add_argument("--video", help="MP4 for cuts and sheet frames (when input is a WAV)")
    ap.add_argument("--cues", help="Cinetic cues.json ({fps, events: [{f, kind, ...}]}) for measuring sync")
    ap.add_argument("--out-dir", required=True, help="output directory (absolute path)")
    ap.add_argument("--detail", choices=["low", "normal", "high"], default="normal",
                    help="how many events the transcript labels (high: up to 96)")
    ap.add_argument("--start", type=float, help="start second (transcript and sheet only)")
    ap.add_argument("--end", type=float, help="end second (transcript and sheet only)")
    ap.add_argument("--no-critique", action="store_true", help="skip the comparison against launch films")
    ap.add_argument("--fast-critique", action="store_true", help="compare without AudioSet labeling or realness")
    ap.add_argument("--width", type=int, default=2400, help="sheet width in pixels")
    ap.add_argument("--threads", type=int, help="CPU threads for the models (EARSHOT_THREADS)")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.threads:
        os.environ["EARSHOT_THREADS"] = str(a.threads)
    from earshot import api
    from earshot.critique import critique_text

    input_path = os.path.abspath(a.input)
    video = os.path.abspath(a.video) if a.video else (input_path if input_path.lower().endswith((".mp4", ".mov")) else None)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    steps = ["measurements", "transcription", "sheet"] + ([] if a.no_critique else ["critique"])
    result: dict = {"input": input_path, "video": video, "cues": a.cues}
    t0 = time.time()

    def show_step(i: int) -> None:
        print(f"[{i}/{len(steps)}] {steps[i - 1]} · {time.time() - t0:.0f} s", file=sys.stderr, flush=True)

    show_step(1)
    result["measurements"] = api.measure(input_path)
    show_step(2)
    timeline = api.timeline(input_path, video=video, cues=a.cues, start=a.start, end=a.end, detail=a.detail)
    result["transcription"] = timeline
    show_step(3)
    sheet = api.sheet(input_path, str(out / "sheet.png"), video=video, cues=a.cues, start=a.start, end=a.end,
                      detail=a.detail, width=a.width, tl=timeline)
    result["sheet"] = sheet.get("png")
    text_output = [timeline["summary"], "", timeline["text"]]
    if not a.no_critique:
        show_step(4)
        critique = api.critique(input_path, video=video, use_ast=not a.fast_critique, use_realness=not a.fast_critique)
        result["critique"] = critique
        text_output += ["", critique_text(critique)]
    (out / "listen.json").write_text(json.dumps(result, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    (out / "listen.txt").write_text("\n".join(text_output) + "\n", encoding="utf-8")
    print(timeline["summary"])
    print(f"outputs: {out / 'listen.json'} · {out / 'listen.txt'} · {result['sheet']} ({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
