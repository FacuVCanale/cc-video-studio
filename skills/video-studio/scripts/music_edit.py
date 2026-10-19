#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy>=2.0",
#   "soundfile>=0.12",
#   "scipy>=1.13",
#   "tqdm>=4.66",
# ]
# ///
"""Edit a song to a film duration using only bar-aligned source cuts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from tqdm import tqdm

from beats import analyze_audio


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(np.asarray(audio, dtype=np.float64)))))


def _active_end(audio: np.ndarray, sample_rate: int, threshold_dbfs: float) -> int:
    mono = audio.mean(axis=1)
    block = max(1, round(sample_rate * 0.02))
    count = len(mono) // block
    if not count:
        return len(audio)
    levels = np.sqrt(np.mean(mono[: count * block].reshape(count, block) ** 2, axis=1))
    active = np.flatnonzero(levels > 10 ** (threshold_dbfs / 20))
    return min(len(audio), int((active[-1] + 1) * block)) if len(active) else len(audio)


def _bar_bounds(analysis: dict[str, object], length: int, sample_rate: int, end_sample: int) -> list[tuple[int, int]]:
    starts = sorted({round(float(value) * sample_rate) for value in analysis["downbeats_s"] if float(value) * sample_rate < end_sample})
    beat_s = float(analysis["beat_s"])
    bar_samples = round(beat_s * int(analysis["beats_per_bar"]) * sample_rate)
    if not starts:
        starts = [0]
    bounds: list[tuple[int, int]] = []
    for position, start in enumerate(starts):
        next_start = starts[position + 1] if position + 1 < len(starts) else start + bar_samples
        stop = min(next_start, end_sample, length)
        if stop - start >= min(bar_samples // 4, round(sample_rate * 0.25)):
            bounds.append((start, stop))
    return bounds


def plan_bars(
    source_bars: list[tuple[int, int]],
    energies: list[float],
    target_bar_count: int,
    climax_bar: int,
    inserts: list[tuple[int, int]] | None = None,
) -> list[int]:
    """Choose source bar indices, reserving the song's own last bar for the film's final bar."""
    if len(source_bars) < 2 or target_bar_count < 2:
        raise ValueError("source and film must each contain at least two bars")
    climax_source = int(np.argmax(energies[:-1]))
    climax_bar = min(max(0, climax_bar), target_bar_count - 2)
    plan: list[int] = []
    for target in range(target_bar_count - 1):
        source = climax_source + target - climax_bar
        if source < 0:
            source %= max(1, climax_source + 1)
        if source >= len(source_bars) - 1:
            source = climax_source + (source - climax_source) % max(1, len(source_bars) - 1 - climax_source)
        plan.append(source)
    for at, count in sorted(inserts or [], reverse=True):
        if not 0 <= at < len(plan) or count <= 0:
            raise ValueError(f"invalid insert at bar {at} for {count} bars")
        repeated = [plan[min(at + offset, len(plan) - 1)] for offset in range(count)]
        plan[at:at] = repeated
        del plan[-count:]
    plan.append(len(source_bars) - 1)
    return plan


def render_plan(
    audio: np.ndarray,
    source_bars: list[tuple[int, int]],
    plan: list[int],
    target_samples: int,
    sample_rate: int,
    crossfade_ms: float,
    grid_tolerance_ms: float,
) -> np.ndarray:
    channels = audio.shape[1]
    pieces: list[np.ndarray] = []
    for index in plan:
        start, stop = source_bars[index]
        piece = audio[start:stop].copy()
        if not len(piece):
            raise ValueError(f"empty source bar {index}")
        pieces.append(piece)
    result = np.concatenate(pieces, axis=0) if pieces else np.zeros((0, channels))
    tolerance = round(grid_tolerance_ms * sample_rate / 1000)
    if abs(len(result) - target_samples) > tolerance:
        raise ValueError("selected whole bars do not match the film duration; adjust the film to the song grid")
    # Sample rounding can leave a sub-tolerance discrepancy. It is corrected at
    # the opening so the source ending remains byte-for-byte complete.
    if len(result) < target_samples:
        result = np.concatenate((np.repeat(result[:1], target_samples - len(result), axis=0), result))
    elif len(result) > target_samples:
        result = result[len(result) - target_samples :]
    # Crossfade around each discontinuous bar boundary. Both source signals are
    # sampled across the same timeline window, so the overlap neither inserts
    # nor removes samples and the musical grid stays fixed.
    fade = max(0, round(crossfade_ms * sample_rate / 1000))
    boundary = target_samples - sum(len(piece) for piece in pieces)
    for position, (left_index, right_index) in enumerate(zip(plan[:-1], plan[1:])):
        boundary += len(pieces[position])
        left_stop = source_bars[left_index][1]
        right_start = source_bars[right_index][0]
        if right_start == left_stop or fade == 0:
            continue
        start = max(0, boundary - fade)
        stop = min(len(result), boundary + fade)
        relative = np.arange(start - boundary, stop - boundary)
        left_samples = np.clip(left_stop + relative, 0, len(audio) - 1)
        right_samples = np.clip(right_start + relative, 0, len(audio) - 1)
        phase = np.linspace(0, np.pi / 2, len(relative), endpoint=True)[:, None]
        result[start:stop] = audio[left_samples] * np.cos(phase) + audio[right_samples] * np.sin(phase)
    return result


def _parse_inserts(rows: list[dict[str, object]], fps: float, analysis: dict[str, object]) -> list[tuple[int, int]]:
    bar_s = float(analysis["beat_s"]) * int(analysis["beats_per_bar"])
    return [(int(round(float(row["frame"]) / fps / bar_s)), int(row["bars"])) for row in rows]


def _apply_downbeat_override(analysis: dict[str, object], offset: int) -> dict[str, object]:
    beats_per_bar = int(analysis["beats_per_bar"])
    if not 0 <= offset < beats_per_bar:
        raise ValueError("downbeat offset must be within the bar")
    updated = dict(analysis)
    beats = [float(value) for value in analysis["beats_s"]]
    fps = float(analysis["fps"])
    downbeats = beats[offset::beats_per_bar]
    updated.update(
        downbeat_offset=offset,
        downbeat_source="override",
        suitable_for_bar_cuts=True,
        downbeats_s=downbeats,
        downbeats_frames=[int(round(value * fps)) for value in downbeats],
    )
    return updated


def edit_music(
    audio: np.ndarray,
    sample_rate: int,
    analysis: dict[str, object],
    *,
    duration_s: float,
    climax_s: float,
    crossfade_ms: float = 20.0,
    silence_threshold_dbfs: float = -55.0,
    grid_tolerance_ms: float = 15.0,
    inserts: list[tuple[int, int]] | None = None,
) -> tuple[np.ndarray, dict[str, object]]:
    if analysis.get("suitable_for_bar_cuts") is False:
        raise ValueError("downbeat phase is ambiguous; provide an explicit downbeat offset before editing")
    target_samples = round(duration_s * sample_rate)
    bar_s = float(analysis["beat_s"]) * int(analysis["beats_per_bar"])
    target_bar_count = max(2, int(round(duration_s / bar_s)))
    if abs(duration_s - target_bar_count * bar_s) * 1000 > grid_tolerance_ms:
        raise ValueError("film duration is not an integral number of song bars")
    end_sample = _active_end(audio, sample_rate, silence_threshold_dbfs)
    source_bars = _bar_bounds(analysis, len(audio), sample_rate, end_sample)
    energies = [_rms(audio[start:stop]) for start, stop in source_bars]
    climax_bar = min(target_bar_count - 2, max(0, int(round(climax_s / bar_s))))
    plan = plan_bars(source_bars, energies, target_bar_count, climax_bar, inserts)
    edited = render_plan(audio, source_bars, plan, target_samples, sample_rate, crossfade_ms, grid_tolerance_ms)
    report = {
        "duration_s": duration_s,
        "bar_s": round(bar_s, 7),
        "crossfade_ms": crossfade_ms,
        "grid_tolerance_ms": grid_tolerance_ms,
        "climax": {"requested_s": climax_s, "target_bar": climax_bar, "source_bar": plan[climax_bar]},
        "source_final_bar": len(source_bars) - 1,
        "plan_source_bars": plan,
        "cuts_source_s": [round(source_bars[index][0] / sample_rate, 6) for index in plan],
        "ends_with_song_final_bar": plan[-1] == len(source_bars) - 1,
        "contains_padding_silence": False,
    }
    return edited, report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="absolute source audio path")
    parser.add_argument("--cues", type=Path, required=True, help="absolute Cinetic cues.json path")
    parser.add_argument("--climax-cue", required=True, help="name from cues.json cue")
    parser.add_argument("--output", type=Path, required=True, help="absolute edited WAV path")
    parser.add_argument("--report", type=Path, help="absolute JSON report path")
    parser.add_argument("--duration", type=float, help="film duration in seconds; defaults to total/fps")
    parser.add_argument("--analysis", type=Path, help="optional beats.py JSON")
    parser.add_argument("--inserts", type=Path, help='optional JSON: [{"frame": 120, "bars": 2}]')
    parser.add_argument("--crossfade-ms", type=float, default=20.0)
    parser.add_argument("--silence-threshold-dbfs", type=float, default=-55.0)
    parser.add_argument("--grid-tolerance-ms", type=float, default=15.0)
    parser.add_argument("--downbeat-offset", type=int, help="manual beat offset within each bar, starting at zero")
    parser.add_argument("--min-downbeat-confidence", type=float, default=0.1)
    parser.add_argument("--subtype", default="PCM_24")
    return parser


def main() -> int:
    args = _parser().parse_args()
    source = args.audio.expanduser().resolve()
    cues_path = args.cues.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_file() or not cues_path.is_file():
        raise SystemExit("audio and cues must be existing absolute paths")
    cues = json.loads(cues_path.read_text(encoding="utf-8"))
    fps = float(cues["fps"])
    if args.climax_cue not in cues.get("cue", {}):
        raise SystemExit(f"unknown climax cue: {args.climax_cue}")
    duration = args.duration if args.duration is not None else float(cues["total"]) / fps
    if duration <= 0 or args.crossfade_ms < 0 or args.grid_tolerance_ms < 0:
        raise SystemExit("duration must be positive; crossfade-ms and grid-tolerance-ms cannot be negative")
    with tqdm(total=4, unit="stage", desc="Editing music") as progress:
        audio, sample_rate = sf.read(source, always_2d=True, dtype="float64")
        progress.update()
        if args.analysis:
            analysis_path = args.analysis.expanduser().resolve()
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        else:
            analysis = analyze_audio(
                audio,
                sample_rate,
                fps=fps,
                downbeat_offset=args.downbeat_offset,
                min_downbeat_confidence=args.min_downbeat_confidence,
            )
        if args.downbeat_offset is not None and args.analysis:
            analysis = _apply_downbeat_override(analysis, args.downbeat_offset)
        progress.update()
        inserts: list[tuple[int, int]] = []
        if args.inserts:
            rows = json.loads(args.inserts.expanduser().resolve().read_text(encoding="utf-8"))
            inserts = _parse_inserts(rows, fps, analysis)
        climax_s = float(cues["cue"][args.climax_cue]) / fps
        edited, report = edit_music(
            audio,
            sample_rate,
            analysis,
            duration_s=duration,
            climax_s=climax_s,
            crossfade_ms=args.crossfade_ms,
            silence_threshold_dbfs=args.silence_threshold_dbfs,
            grid_tolerance_ms=args.grid_tolerance_ms,
            inserts=inserts,
        )
        progress.update()
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, edited, sample_rate, subtype=args.subtype)
        report.update({"source": str(source), "cues": str(cues_path), "output": str(output), "sample_rate": sample_rate})
        report_path = (args.report or output.with_suffix(".json")).expanduser().resolve()
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        progress.update()
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
