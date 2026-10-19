#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "scipy>=1.12", "soundfile>=0.12", "tqdm>=4.66", "kokoro>=0.9.4", "faster-whisper>=1.1"]
# ///
"""Generate voice-over in multi-line takes and cut it while preserving context."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy import signal
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from silence_cut import line_word_ranges, plain_word, plan_cuts, words_from_characters  # noqa: E402


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def group_takes(lines: list[dict[str, Any]], minimum: int, maximum: int) -> list[list[dict[str, Any]]]:
    """Group by ``take`` when present; otherwise build consecutive groups of 2--4 lines."""
    if not lines:
        return []
    if any("take" in line for line in lines):
        groups: list[list[dict[str, Any]]] = []
        by_name: dict[str, list[dict[str, Any]]] = {}
        for line in lines:
            name = str(line.get("take", line["id"]))
            if name not in by_name:
                by_name[name] = []
                groups.append(by_name[name])
            by_name[name].append(line)
        invalid = [(str(group[0].get("take", group[0]["id"])), len(group)) for group in groups
                   if not minimum <= len(group) <= maximum]
        if invalid:
            raise ValueError(f"takes must contain between {minimum} and {maximum} lines: {invalid}")
        return groups
    groups = []
    cursor = 0
    while cursor < len(lines):
        remaining = len(lines) - cursor
        size = min(maximum, remaining)
        if 0 < remaining - size < minimum:
            size -= minimum - (remaining - size)
        groups.append(lines[cursor : cursor + size])
        cursor += size
    return groups


def take_text(lines: list[dict[str, Any]]) -> str:
    return " ".join(str(line.get("tts", line["text"])).strip() for line in lines)


def prepare_fal(args: argparse.Namespace, groups: list[list[dict[str, Any]]]) -> Path:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    batch = args.batch or args.output_dir / "fal-takes.jsonl"
    with batch.open("w", encoding="utf-8") as handle:
        for index, lines in enumerate(groups, 1):
            name = str(lines[0].get("take", f"take-{index:03d}"))
            item = {
                "id": name,
                "endpoint": args.endpoint,
                "input": {
                    "text": take_text(lines),
                    "voice": args.voice,
                    "timestamps": True,
                },
                "out": str((args.output_dir / f"{name}.{args.audio_format}").resolve()),
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return batch.resolve()


def run_fal(args: argparse.Namespace, batch: Path) -> None:
    fal_script = args.fal_script.resolve()
    command = ["uv", "run", str(fal_script), str(batch), "--fal-dir", str(args.fal_dir.resolve())]
    if args.estimate:
        command.append("--estimate")
    subprocess.run(command, check=True)


def synthesize_kokoro(args: argparse.Namespace, groups: list[list[dict[str, Any]]]) -> None:
    try:
        from kokoro import KPipeline  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit("Kokoro is not installed. Run: uv run --with kokoro tts_takes.py generate ...") from exc
    pipeline = KPipeline(lang_code=args.language)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[tuple[Path, list[dict[str, Any]]]] = []
    for index, lines in enumerate(tqdm(groups, desc="Takes", unit="take"), 1):
        name = str(lines[0].get("take", f"take-{index:03d}"))
        chunks = [audio for _, _, audio in pipeline(take_text(lines), voice=args.voice, speed=args.speed)]
        if not chunks:
            raise RuntimeError(f"Kokoro did not produce audio for {name}")
        path = args.output_dir / f"{name}.wav"
        sf.write(path, np.concatenate(chunks).astype(np.float32), args.sample_rate)
        generated.append((path, lines))
    transcribe_kokoro_takes(args, generated)


def transcribe_kokoro_takes(args: argparse.Namespace, generated: list[tuple[Path, list[dict[str, Any]]]]) -> None:
    """Get actual word timings; never estimate cuts from character counts."""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "word timings are required to cut Kokoro takes; run "
            "uv run --with kokoro --with faster-whisper tts_takes.py generate ..."
        ) from exc
    model = WhisperModel(args.whisper_model, device=args.whisper_device, compute_type=args.whisper_compute_type)
    for path, lines in tqdm(generated, desc="Aligning", unit="take"):
        segments, _ = model.transcribe(str(path), language=args.whisper_language, word_timestamps=True,
                                       condition_on_previous_text=False, temperature=0)
        timed = []
        for segment in segments:
            for word in segment.words or []:
                timed.append({"word": word.word.strip(), "start": float(word.start), "end": float(word.end)})
        cut_from_word_times(path, lines, timed, args)


def flatten_fal_timestamps(log: dict[str, Any]) -> tuple[str, list[float], list[float]]:
    blocks = log.get("timestamps", log.get("output", {}).get("timestamps", []))
    chars: list[str] = []
    starts: list[float] = []
    ends: list[float] = []
    for block in blocks:
        block_chars = block.get("characters", [])
        block_starts = block.get("character_start_times_seconds", [])
        block_ends = block.get("character_end_times_seconds", block_starts)
        chars.extend(block_chars)
        starts.extend(float(value) for value in block_starts)
        ends.extend(float(value) for value in block_ends)
    if not chars or len(chars) != len(starts) or len(chars) != len(ends):
        raise ValueError("the log does not contain valid character timings")
    return "".join(chars), starts, ends


def fal_word_timestamps(log: dict[str, Any]) -> list[dict[str, Any]] | None:
    blocks = log.get("timestamps", log.get("output", {}).get("timestamps"))
    if not isinstance(blocks, list) or not blocks:
        return None
    if isinstance(blocks[0], dict) and ("word" in blocks[0] or "text" in blocks[0]):
        return [{"word": str(item.get("word", item.get("text", ""))).strip(),
                 "start": float(item.get("start", item.get("start_time", 0))),
                 "end": float(item.get("end", item.get("end_time", 0)))} for item in blocks]
    return None


def cut_from_word_times(source: Path, lines: list[dict[str, Any]], timed: list[dict[str, Any]], args: argparse.Namespace) -> None:
    """Cut at actual silence between lines (silence_cut.py), never at the alignment midpoint."""
    audio, sample_rate = read_audio(source)
    timed = [item for item in timed if plain_word(item["word"])]
    plan = plan_cuts(audio, sample_rate, lines, timed,
                     db=getattr(args, "silence_db", -45.0), drift=getattr(args, "drift", 0.3),
                     min_gap_ms=getattr(args, "min_gap_ms", 60.0), edge_ms=getattr(args, "edge_ms", 30.0),
                     head_ms=args.head_ms, tail_ms=args.tail_ms)
    for line, row in zip(lines, plan):
        word_a, word_b = row["words"]
        start, end = timed[word_a]["start"], timed[word_b - 1]["end"]
        write_segment(audio, sample_rate, row["left"], row["right"], line, timed[word_a:word_b], args,
                      source.stem, start, end)


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        return sf.read(path, always_2d=True)
    except (RuntimeError, sf.LibsndfileError):
        with tempfile.TemporaryDirectory(prefix="tts-decode-") as directory:
            decoded = Path(directory) / "take.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-c:a", "pcm_f32le", str(decoded)], check=True)
            return sf.read(decoded, always_2d=True)


def write_segment(audio: np.ndarray, sample_rate: int, left: float, right: float, line: dict[str, Any],
                  words: list[dict[str, Any]], args: argparse.Namespace, take: str, start: float, end: float) -> None:
    segment = audio[round(left * sample_rate) : round(right * sample_rate)].copy()
    fade_in = min(len(segment), round(args.fade_in_ms * sample_rate / 1000))
    fade_out = min(len(segment), round(args.fade_out_ms * sample_rate / 1000))
    if fade_in:
        segment[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    if fade_out:
        segment[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    divisor = math.gcd(sample_rate, args.output_sample_rate)
    segment = signal.resample_poly(segment, args.output_sample_rate // divisor, sample_rate // divisor, axis=0)
    sf.write(args.output_dir / f"{line['id']}.wav", segment.astype(np.float32), args.output_sample_rate, subtype="FLOAT")
    marks = [{"word": item["word"], "start": round(item["start"] - left, 4), "end": round(item["end"] - left, 4)} for item in words]
    (args.output_dir / f"{line['id']}.words.json").write_text(json.dumps(marks, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata = {"take": take, "start_s": round(start, 4), "end_s": round(end, 4)}
    (args.output_dir / f"{line['id']}.take.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def cut_fal(args: argparse.Namespace, groups: list[list[dict[str, Any]]]) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for index, lines in enumerate(tqdm(groups, desc="Cuts", unit="take"), 1):
        name = str(lines[0].get("take", f"take-{index:03d}"))
        source = args.takes_dir / f"{name}.{args.audio_format}"
        log = read_json(args.logs_dir / f"{name}.json")
        timed_words = fal_word_timestamps(log)
        if timed_words is None:
            text, starts, ends = flatten_fal_timestamps(log)
            timed_words = words_from_characters(text, starts, ends)
        cut_from_word_times(source, lines, timed_words, args)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("generate", "prepare-fal", "cut-fal"))
    result.add_argument("--script", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--backend", choices=("kokoro", "fal"), default="kokoro")
    result.add_argument("--voice", default="af_heart")
    result.add_argument("--speed", type=float, default=1.0)
    result.add_argument("--language", default="e", help="Kokoro language code")
    result.add_argument("--sample-rate", type=int, default=24000)
    result.add_argument("--output-sample-rate", type=int, default=48000, help="sample rate of per-line WAV files for mixing")
    result.add_argument("--min-lines", type=int, default=2)
    result.add_argument("--max-lines", type=int, default=4)
    result.add_argument("--batch", type=Path)
    result.add_argument("--endpoint", default="fal-ai/elevenlabs/tts/eleven-v3")
    result.add_argument("--audio-format", default="mp3", help="actual extension returned by fal (Eleven v3 uses mp3)")
    result.add_argument("--fal-script", type=Path, default=Path(__file__).with_name("fal_run.py"))
    result.add_argument("--fal-dir", type=Path, default=Path("audio/fal"))
    result.add_argument("--estimate", action="store_true")
    result.add_argument("--takes-dir", type=Path)
    result.add_argument("--logs-dir", type=Path)
    result.add_argument("--head-ms", type=float, default=150)
    result.add_argument("--tail-ms", type=float, default=250)
    result.add_argument("--fade-in-ms", type=float, default=10)
    result.add_argument("--fade-out-ms", type=float, default=60)
    result.add_argument("--silence-db", type=float, default=-45.0, help="speech threshold used to find the gap between lines")
    result.add_argument("--drift", type=float, default=0.3, help="alignment tolerance when finding the gap (s)")
    result.add_argument("--min-gap-ms", type=float, default=60.0, help="minimum gap for cutting at silence")
    result.add_argument("--edge-ms", type=float, default=30.0, help="minimum silence between the cut and speech")
    result.add_argument("--whisper-model", default="small")
    result.add_argument("--whisper-device", default="auto")
    result.add_argument("--whisper-compute-type", default="auto")
    result.add_argument("--whisper-language", default="es")
    return result


def main() -> None:
    args = parser().parse_args()
    args.script = args.script.resolve()
    args.output_dir = args.output_dir.resolve()
    if not 1 <= args.min_lines <= args.max_lines:
        raise SystemExit("--min-lines must be at least 1 and no greater than --max-lines")
    groups = group_takes(read_json(args.script), args.min_lines, args.max_lines)
    if args.action == "prepare-fal":
        print(prepare_fal(args, groups))
    elif args.action == "generate" and args.backend == "fal":
        batch = prepare_fal(args, groups)
        run_fal(args, batch)
    elif args.action == "generate":
        synthesize_kokoro(args, groups)
    else:
        if args.takes_dir is None or args.logs_dir is None:
            raise SystemExit("cut-fal requires --takes-dir and --logs-dir")
        args.takes_dir = args.takes_dir.resolve()
        args.logs_dir = args.logs_dir.resolve()
        cut_fal(args, groups)


if __name__ == "__main__":
    main()
