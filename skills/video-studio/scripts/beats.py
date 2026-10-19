#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy>=2.0",
#   "scipy>=1.13",
#   "soundfile>=0.12",
#   "tqdm>=4.66",
# ]
# ///
"""Measure a song's tempo, beat grid, bars, key, and kick alignment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from tqdm import tqdm


MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _mono(audio: np.ndarray) -> np.ndarray:
    data = np.asarray(audio, dtype=np.float64)
    return data if data.ndim == 1 else data.mean(axis=1)


def _onset_envelope(audio: np.ndarray, sample_rate: int, cutoff_hz: float, hop: int) -> tuple[np.ndarray, float]:
    nyquist = sample_rate / 2
    cutoff = min(cutoff_hz, nyquist * 0.95)
    sos = signal.butter(4, cutoff / nyquist, btype="lowpass", output="sos")
    low = signal.sosfiltfilt(sos, _mono(audio))
    frame = max(hop * 2, int(sample_rate * 0.04))
    if len(low) < frame:
        raise ValueError("audio is too short for beat analysis")
    energy = np.convolve(low * low, np.ones(frame) / frame, mode="valid")[::hop]
    envelope = np.maximum(0.0, np.diff(np.sqrt(energy + 1e-15), prepend=np.sqrt(energy[0] + 1e-15)))
    return envelope, hop / sample_rate


def _tempo(envelope: np.ndarray, step_s: float, min_bpm: float, max_bpm: float) -> float:
    centered = envelope - np.mean(envelope)
    corr = signal.fftconvolve(centered, centered[::-1], mode="full")[len(centered) - 1 :]
    min_lag = max(1, int(round(60 / max_bpm / step_s)))
    max_lag = min(len(corr) - 1, int(round(60 / min_bpm / step_s)))
    if max_lag <= min_lag:
        raise ValueError("audio is too short for the requested BPM range")
    lag_index = min_lag + int(np.argmax(corr[min_lag : max_lag + 1]))
    lag = float(lag_index)
    if 0 < lag_index < len(corr) - 1:
        left, center, right = corr[lag_index - 1 : lag_index + 2]
        denominator = left - 2 * center + right
        if abs(denominator) > 1e-12:
            lag += float(np.clip(0.5 * (left - right) / denominator, -0.5, 0.5))
    return 60.0 / (lag * step_s)


def _kick_grid(envelope: np.ndarray, step_s: float, bpm: float, duration: float) -> tuple[np.ndarray, np.ndarray]:
    period = 60.0 / bpm
    distance = max(1, int(period / step_s * 0.55))
    prominence = max(float(np.std(envelope)) * 0.45, 1e-12)
    peaks, _ = signal.find_peaks(envelope, distance=distance, prominence=prominence)
    attacks = peaks.astype(float) * step_s
    if not len(attacks):
        raise ValueError("no low-frequency attacks were detected")
    phases = np.mod(attacks, period)
    phase_scores = np.array(
        [np.sum(np.exp(-0.5 * (np.minimum(abs(phases - p), period - abs(phases - p)) / 0.025) ** 2)) for p in phases]
    )
    phase = float(phases[int(np.argmax(phase_scores))])
    beats = np.arange(phase, duration + period * 0.25, period)
    beats = beats[(beats >= 0) & (beats <= duration)]
    return beats, attacks


def _downbeat_phase(
    envelope: np.ndarray, step_s: float, beats: np.ndarray, beats_per_bar: int
) -> tuple[int, float, list[float]]:
    """Choose the bar phase whose low-frequency attacks are strongest on average."""
    radius = max(1, round(0.025 / step_s))
    strengths: list[float] = []
    for beat in beats:
        center = int(round(beat / step_s))
        start = max(0, center - radius)
        stop = min(len(envelope), center + radius + 1)
        strengths.append(float(np.max(envelope[start:stop])) if stop > start else 0.0)
    phase_scores = [float(np.mean(strengths[offset::beats_per_bar])) for offset in range(beats_per_bar)]
    order = np.argsort(phase_scores)[::-1]
    offset = int(order[0])
    best = phase_scores[offset]
    second = phase_scores[int(order[1])] if len(order) > 1 else 0.0
    confidence = max(0.0, (best - second) / (best + 1e-15))
    return offset, confidence, phase_scores


def _key(audio: np.ndarray, sample_rate: int) -> dict[str, object]:
    mono = _mono(audio)
    size = min(4096, max(256, 2 ** int(np.floor(np.log2(max(256, len(mono)))))))
    hop = max(128, size // 4)
    frequencies, _, spectrum = signal.stft(mono, fs=sample_rate, nperseg=size, noverlap=size - hop)
    magnitudes = abs(spectrum) ** 2
    valid = frequencies >= 40
    midi = np.rint(69 + 12 * np.log2(frequencies[valid] / 440.0)).astype(int)
    chroma = np.zeros(12)
    for pitch_class in range(12):
        chroma[pitch_class] = magnitudes[valid][midi % 12 == pitch_class].sum()
    scores: list[tuple[float, int, str]] = []
    for tonic in range(12):
        scores.append((float(np.corrcoef(chroma, np.roll(MAJOR_PROFILE, tonic))[0, 1]), tonic, "major"))
        scores.append((float(np.corrcoef(chroma, np.roll(MINOR_PROFILE, tonic))[0, 1]), tonic, "minor"))
    scores.sort(reverse=True)
    score, tonic, mode = scores[0]
    return {"tonic": NOTE_NAMES[tonic], "mode": mode, "correlation": round(score, 4)}


def analyze_audio(
    audio: np.ndarray,
    sample_rate: int,
    *,
    fps: float = 60.0,
    min_bpm: float = 70.0,
    max_bpm: float = 180.0,
    kick_cutoff_hz: float = 150.0,
    beats_per_bar: int = 4,
    downbeat_offset: int | None = None,
    min_downbeat_confidence: float = 0.1,
) -> dict[str, object]:
    """Return a deterministic musical grid measured from low-frequency attacks."""
    duration = len(audio) / sample_rate
    hop = max(64, round(sample_rate * 0.005))
    envelope, step_s = _onset_envelope(audio, sample_rate, kick_cutoff_hz, hop)
    bpm = _tempo(envelope, step_s, min_bpm, max_bpm)
    beats, attacks = _kick_grid(envelope, step_s, bpm, duration)
    if len(beats) < beats_per_bar:
        raise ValueError("fewer than one full bar was detected")
    deviations = np.array([np.min(abs(attacks - beat)) for beat in beats])
    detected_offset, downbeat_confidence, downbeat_scores = _downbeat_phase(
        envelope, step_s, beats, beats_per_bar
    )
    if downbeat_offset is not None and not 0 <= downbeat_offset < beats_per_bar:
        raise ValueError("downbeat offset must be within the bar")
    chosen_offset = detected_offset if downbeat_offset is None else downbeat_offset
    suitable_for_bar_cuts = downbeat_offset is not None or downbeat_confidence >= min_downbeat_confidence
    downbeats = beats[chosen_offset::beats_per_bar]
    return {
        "duration_s": round(duration, 6),
        "fps": fps,
        "tempo_bpm": round(bpm, 4),
        "beat_s": round(60.0 / bpm, 7),
        "beats_per_bar": beats_per_bar,
        "downbeat_offset": chosen_offset,
        "detected_downbeat_offset": detected_offset,
        "downbeat_source": "measured" if downbeat_offset is None else "override",
        "downbeat_confidence": round(downbeat_confidence, 4),
        "min_downbeat_confidence": min_downbeat_confidence,
        "suitable_for_bar_cuts": suitable_for_bar_cuts,
        "downbeat_phase_scores": [round(score, 8) for score in downbeat_scores],
        "beats_s": [round(float(value), 6) for value in beats],
        "beats_frames": [int(round(value * fps)) for value in beats],
        "downbeats_s": [round(float(value), 6) for value in downbeats],
        "downbeats_frames": [int(round(value * fps)) for value in downbeats],
        "key": _key(audio, sample_rate),
        "kick_verification": {
            "cutoff_hz": kick_cutoff_hz,
            "attacks": len(attacks),
            "median_deviation_ms": round(float(np.median(deviations)) * 1000, 3),
            "p90_deviation_ms": round(float(np.percentile(deviations, 90)) * 1000, 3),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="absolute path to a WAV, FLAC, or OGG song")
    parser.add_argument("--output", type=Path, help="absolute JSON path; defaults beside the input")
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--min-bpm", type=float, default=70.0)
    parser.add_argument("--max-bpm", type=float, default=180.0)
    parser.add_argument("--kick-cutoff-hz", type=float, default=150.0)
    parser.add_argument("--beats-per-bar", type=int, default=4)
    parser.add_argument("--downbeat-offset", type=int, help="manual beat offset within each bar, starting at zero")
    parser.add_argument("--min-downbeat-confidence", type=float, default=0.1)
    return parser


def main() -> int:
    args = _parser().parse_args()
    source = args.audio.expanduser().resolve()
    if not source.is_absolute() or not source.is_file():
        raise SystemExit(f"audio does not exist: {source}")
    output = (args.output or source.with_suffix(".beats.json")).expanduser().resolve()
    if args.min_bpm <= 0 or args.max_bpm <= args.min_bpm or args.fps <= 0 or args.beats_per_bar <= 0:
        raise SystemExit("fps, BPM bounds, and beats-per-bar must be positive")
    with tqdm(total=3, unit="stage", desc="Analyzing beats") as progress:
        audio, sample_rate = sf.read(source, always_2d=True, dtype="float64")
        progress.update()
        result = analyze_audio(
            audio,
            sample_rate,
            fps=args.fps,
            min_bpm=args.min_bpm,
            max_bpm=args.max_bpm,
            kick_cutoff_hz=args.kick_cutoff_hz,
            beats_per_bar=args.beats_per_bar,
            downbeat_offset=args.downbeat_offset,
            min_downbeat_confidence=args.min_downbeat_confidence,
        )
        progress.update()
        output.parent.mkdir(parents=True, exist_ok=True)
        result["source"] = str(source)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        progress.update()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
