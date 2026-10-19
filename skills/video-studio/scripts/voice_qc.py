#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "scipy>=1.12", "soundfile>=0.12", "tqdm>=4.66", "faster-whisper>=1.1"]
# ///
"""Compare voice-over against the script and check for consistent pitch."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf
from scipy import signal
from tqdm import tqdm


def normalize_words(text: str, equivalences: dict[str, str] | None = None) -> list[str]:
    equivalences = equivalences or {}
    plain = "".join(character for character in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(character) != "Mn")
    tokens = re.sub(r"[^a-z0-9ñ]+", " ", plain).split()
    return [mapped for token in tokens if (mapped := equivalences.get(token, token))]


def compare_text(wanted: str, heard: str, equivalences: dict[str, str]) -> tuple[float, list[dict[str, str]]]:
    expected = normalize_words(wanted, equivalences)
    actual = normalize_words(heard, equivalences)
    matcher = difflib.SequenceMatcher(None, expected, actual, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    differences = [
        {"type": opcode, "script": " ".join(expected[a:b]), "heard": " ".join(actual[c:d])}
        for opcode, a, b, c, d in matcher.get_opcodes()
        if opcode != "equal"
    ]
    return 100 * matched / max(1, len(expected)), differences


def pitch_stats(path: Path, min_hz: float = 70, max_hz: float = 350, frame_ms: float = 40, hop_ms: float = 10) -> dict[str, float | None]:
    audio, sample_rate = sf.read(path, always_2d=True)
    mono = audio.mean(axis=1).astype(float)
    mono = signal.sosfilt(signal.butter(2, 60, "highpass", fs=sample_rate, output="sos"), mono)
    frame = max(16, round(frame_ms * sample_rate / 1000))
    hop = max(1, round(hop_ms * sample_rate / 1000))
    pitches = []
    for start in range(0, max(0, len(mono) - frame + 1), hop):
        chunk = mono[start : start + frame]
        if np.sqrt(np.mean(chunk**2)) < 1e-4:
            continue
        chunk = (chunk - chunk.mean()) * np.hanning(len(chunk))
        correlation = signal.correlate(chunk, chunk, mode="full", method="fft")[len(chunk) - 1 :]
        low_lag = max(1, int(sample_rate / max_hz))
        high_lag = min(len(correlation) - 1, int(sample_rate / min_hz))
        lag = low_lag + int(np.argmax(correlation[low_lag : high_lag + 1]))
        if correlation[lag] > correlation[0] * 0.25:
            pitches.append(sample_rate / lag)
    if not pitches:
        return {"f0_median_hz": None, "f0_p90_hz": None}
    return {"f0_median_hz": round(float(np.median(pitches)), 2), "f0_p90_hz": round(float(np.percentile(pitches, 90)), 2)}


def make_transcriber(model_name: str, language: str, device: str, compute_type: str) -> Callable[[Path], str]:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit("faster-whisper is missing; run: uv run --with faster-whisper voice_qc.py ...") from exc
    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(path: Path) -> str:
        segments, _ = model.transcribe(str(path), language=language, condition_on_previous_text=False, temperature=0)
        return " ".join(segment.text.strip() for segment in segments).strip()

    return transcribe


def approved_baseline(approved: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    medians = [item.get("f0_median_hz") for item in approved if item.get("f0_median_hz")]
    p90s = [item.get("f0_p90_hz") for item in approved if item.get("f0_p90_hz")]
    return (float(np.median(medians)) if medians else None, float(np.median(p90s)) if p90s else None)


def tone_ok(stats: dict[str, float | None], baseline: tuple[float | None, float | None], tolerance_pct: float) -> bool:
    values = (stats["f0_median_hz"], stats["f0_p90_hz"])
    return all(reference is None or (value is not None and abs(value - reference) / reference * 100 <= tolerance_pct)
               for value, reference in zip(values, baseline))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--script", type=Path, required=True)
    result.add_argument("--audio-dir", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--mix", type=Path)
    result.add_argument("--equivalences", type=Path)
    result.add_argument("--approved", type=Path)
    result.add_argument("--model", default="large-v3")
    result.add_argument("--language", default="es")
    result.add_argument("--device", default="auto")
    result.add_argument("--compute-type", default="auto")
    result.add_argument("--minimum", type=float, default=95.0)
    result.add_argument("--pitch-tolerance-pct", type=float, default=18.0)
    result.add_argument("--f0-min", type=float, default=70)
    result.add_argument("--f0-max", type=float, default=350)
    result.add_argument("--frame-ms", type=float, default=40)
    result.add_argument("--hop-ms", type=float, default=10)
    return result


def main() -> None:
    args = parser().parse_args()
    script = json.loads(args.script.resolve().read_text(encoding="utf-8"))
    equivalences = json.loads(args.equivalences.resolve().read_text(encoding="utf-8")) if args.equivalences else {}
    approved = json.loads(args.approved.resolve().read_text(encoding="utf-8")) if args.approved else []
    baseline = approved_baseline(approved)
    transcribe = make_transcriber(args.model, args.language, args.device, args.compute_type)
    if args.mix:
        wanted = " ".join(line["text"] for line in script)
        score, differences = compare_text(wanted, transcribe(args.mix.resolve()), equivalences)
        report = {"mode": "mix", "percentage": round(score, 2), "minimum": args.minimum, "differences": differences, "ok": score >= args.minimum}
    else:
        items = []
        for line in tqdm(script, desc="Voice QC", unit="line"):
            path = args.audio_dir.resolve() / f"{line['id']}.wav"
            heard = transcribe(path)
            score, differences = compare_text(line.get("tts", line["text"]), heard, equivalences)
            stats = pitch_stats(path, args.f0_min, args.f0_max, args.frame_ms, args.hop_ms)
            item = {"id": line["id"], "heard": heard, "percentage": round(score, 2), "differences": differences, **stats}
            item["pitch_ok"] = tone_ok(stats, baseline, args.pitch_tolerance_pct)
            item["ok"] = not differences and item["pitch_ok"]
            items.append(item)
        report = {"mode": "lines", "model": args.model, "f0_baseline": baseline, "lines": items, "ok": all(item["ok"] for item in items)}
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
