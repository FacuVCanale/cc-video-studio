#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "scipy>=1.12", "soundfile>=0.12", "pyloudnorm>=0.1.1", "tqdm>=4.66"]
# ///
"""Place voice-over, generate subtitles, and mix with multiband music ducking."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import signal
from tqdm import tqdm

LINK_WORDS = {"a", "al", "de", "del", "el", "la", "las", "los", "un", "una", "y", "o", "en", "por", "para", "con", "sin", "que"}


def cue_seconds(cues: dict[str, Any], anchor: str | list[str]) -> float:
    names = anchor if isinstance(anchor, list) else [anchor]
    for name in names:
        value = cues.get("cue", {}).get(name, cues.get(name))
        if value is not None:
            frame = value[0] if isinstance(value, list) else value
            return float(frame) / float(cues["fps"])
    raise ValueError(f"cue {anchor!r} does not exist")


def read_audio(path: Path, sample_rate: int, allow_resample: bool = False) -> np.ndarray:
    audio, rate = sf.read(path, always_2d=True)
    if rate != sample_rate:
        if not allow_resample:
            raise ValueError(f"{path} is {rate} Hz; approved takes must be {sample_rate} Hz")
        divisor = math.gcd(rate, sample_rate)
        audio = signal.resample_poly(audio, sample_rate // divisor, rate // divisor, axis=0)
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    return audio[:, :2].astype(float)


def place_lines(
    script: list[dict[str, Any]], cues: dict[str, Any], audio_dir: Path, sample_rate: int,
    lead_s: float, gap_s: float, max_shift_s: float, end_margin_s: float,
) -> list[dict[str, Any]]:
    placed: list[dict[str, Any]] = []
    for item in script:
        audio = read_audio(audio_dir / f"{item['id']}.wav", sample_rate)
        nominal = cue_seconds(cues, item["anchor"]) + lead_s + float(item.get("offset_s", 0))
        if "follows_s" in item and placed:
            nominal = placed[-1]["end"] + float(item["follows_s"])
        start = nominal
        if placed and start < placed[-1]["end"] + gap_s:
            shift = placed[-1]["end"] + gap_s - start
            start += shift
        end = start + len(audio) / sample_rate
        film_end = float(cues["total"]) / float(cues["fps"])
        if end > film_end - end_margin_s:
            raise ValueError(f"{item['id']} ends at {end:.3f} s, outside the film ({film_end:.3f} s)")
        marks_path = audio_dir / f"{item['id']}.words.json"
        if not marks_path.exists():
            raise ValueError(f"word marks are missing for {item['id']}: {marks_path}")
        marks = json.loads(marks_path.read_text(encoding="utf-8"))
        if not marks:
            raise ValueError(f"word marks for {item['id']} are empty")
        take_path = audio_dir / f"{item['id']}.take.json"
        take = json.loads(take_path.read_text(encoding="utf-8")) if take_path.exists() else None
        if placed and take and placed[-1].get("take") and take.get("take") == placed[-1]["take"].get("take"):
            natural_gap = max(0.0, float(take.get("start_s", 0)) - float(placed[-1]["take"].get("end_s", 0)))
            required_start = placed[-1]["end"] + max(gap_s, natural_gap)
            if start < required_start:
                start = required_start
                end = start + len(audio) / sample_rate
        total_shift = start - nominal
        if total_shift > max_shift_s:
            raise ValueError(f"{item['id']} requires a {total_shift:.3f} s shift (maximum {max_shift_s:.3f} s)")
        if end > film_end - end_margin_s:
            raise ValueError(f"{item['id']} ends at {end:.3f} s, outside the film ({film_end:.3f} s)")
        placed.append({**item, "audio": audio, "start": start, "end": end, "nominal": nominal, "words": marks, "take": take})
    return placed


def merge_spans(spans: list[tuple[float, float]], bridge_s: float) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] < bridge_s:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def smooth_gate(length: int, spans: list[tuple[float, float]], sample_rate: int, attack_s: float, release_s: float) -> np.ndarray:
    gate = np.zeros(length, dtype=float)
    attack_samples = max(1, round(attack_s * sample_rate))
    release_samples = max(1, round(release_s * sample_rate))
    for start, end in spans:
        start_sample = max(0, round(start * sample_rate))
        end_sample = min(length, round(end * sample_rate))
        attack_begin = max(0, start_sample - attack_samples)
        release_end = min(length, end_sample + release_samples)
        gate[attack_begin:start_sample] = np.maximum(
            gate[attack_begin:start_sample], np.linspace(0, 1, start_sample - attack_begin, endpoint=False)
        )
        gate[start_sample:end_sample] = 1
        gate[end_sample:release_end] = np.maximum(
            gate[end_sample:release_end], np.linspace(1, 0, release_end - end_sample, endpoint=False)
        )
    return gate


def band_duck(audio: np.ndarray, gate: np.ndarray, sample_rate: int, low_hz: float, high_hz: float, low_db: float, mid_db: float, high_db: float) -> np.ndarray:
    low = signal.sosfiltfilt(signal.butter(4, low_hz, "lowpass", fs=sample_rate, output="sos"), audio, axis=0)
    high = signal.sosfiltfilt(signal.butter(4, high_hz, "highpass", fs=sample_rate, output="sos"), audio, axis=0)
    middle = audio - low - high
    gain = lambda amount: np.power(10.0, amount * gate / 20)[:, None]
    return low * gain(-abs(low_db)) + middle * gain(-abs(mid_db)) + high * gain(-abs(high_db))


def low_shelf(audio: np.ndarray, sample_rate: int, frequency: float, gain_db: float) -> np.ndarray:
    if gain_db == 0:
        return audio
    # Low-pass plus residual: a stable shelf that is transparent outside the low band.
    low = signal.sosfiltfilt(signal.butter(2, frequency, "lowpass", fs=sample_rate, output="sos"), audio, axis=0)
    return audio - low + low * 10 ** (gain_db / 20)


def fit(audio: np.ndarray | None, length: int) -> np.ndarray:
    if audio is None:
        return np.zeros((length, 2))
    return np.pad(audio[:length], ((0, max(0, length - len(audio))), (0, 0)))


def master(audio: np.ndarray, sample_rate: int, target_lufs: float, true_peak_dbtp: float, oversample: int) -> np.ndarray:
    meter = pyln.Meter(sample_rate)
    loudness = meter.integrated_loudness(audio)
    if np.isfinite(loudness):
        audio = audio * 10 ** ((target_lufs - loudness) / 20)
    oversampled = signal.resample_poly(audio, oversample, 1, axis=0)
    peak = float(np.max(np.abs(oversampled))) if len(oversampled) else 0
    ceiling = 10 ** (true_peak_dbtp / 20)
    if peak > ceiling:
        audio *= ceiling / peak
    return audio


def master_preserving_voice(voice: np.ndarray, bed: np.ndarray, sample_rate: int, target_lufs: float,
                            wav_peak_dbtp: float, oversample: int, tolerance: float) -> tuple[np.ndarray, float]:
    """Adjust only the bed; the voice bus enters the sum without gain."""
    meter = pyln.Meter(sample_rate)
    voice_lufs = float(meter.integrated_loudness(voice))
    if np.isfinite(voice_lufs) and voice_lufs > target_lufs + tolerance:
        raise RuntimeError(
            f"the approved voice already measures {voice_lufs:.2f} LUFS, above the master target; "
            "level the takes before approving them"
        )
    low, high = 0.0, 1.0
    loudness = float(meter.integrated_loudness(voice + bed * high))
    while loudness < target_lufs and high < 1024:
        high *= 2
        loudness = float(meter.integrated_loudness(voice + bed * high))
    if loudness < target_lufs - tolerance:
        raise RuntimeError("target loudness cannot be reached without modifying the voice bus")
    for _ in range(40):
        gain = (low + high) / 2
        loudness = float(meter.integrated_loudness(voice + bed * gain))
        if loudness < target_lufs:
            low = gain
        else:
            high = gain
    gain = (low + high) / 2
    mixed = voice + bed * gain
    residual_error = float(np.max(np.abs((mixed - bed * gain) - voice)))
    if residual_error > 1e-12:
        raise RuntimeError(f"the voice bus changed within the sum (maximum error {residual_error:.3g})")
    measured_lufs, measured_peak = master_metrics(mixed, sample_rate, oversample)
    if abs(measured_lufs - target_lufs) > tolerance or measured_peak > wav_peak_dbtp + 0.05:
        raise RuntimeError(
            f"the master target cannot be met while preserving voice: {measured_lufs:.2f} LUFS, {measured_peak:.2f} dBTP; "
            "level the takes or bed before mixing"
        )
    return mixed, gain


def master_metrics(audio: np.ndarray, sample_rate: int, oversample: int) -> tuple[float, float]:
    loudness = float(pyln.Meter(sample_rate).integrated_loudness(audio))
    peak = float(np.max(np.abs(signal.resample_poly(audio, oversample, 1, axis=0))))
    return loudness, 20 * np.log10(max(peak, 1e-12))


def voice_over_music_db(voice: np.ndarray, music: np.ndarray, gate: np.ndarray, sample_rate: int,
                        low_hz: float, high_hz: float) -> float | None:
    active = gate > 0.9
    if not np.any(active):
        return None
    band = signal.butter(4, [low_hz, high_hz], "bandpass", fs=sample_rate, output="sos")
    voice_band = signal.sosfilt(band, voice.mean(axis=1))[active]
    music_band = signal.sosfilt(band, music.mean(axis=1))[active]
    voice_power = float(np.mean(voice_band**2))
    music_power = float(np.mean(music_band**2))
    if voice_power < 1e-12 or music_power < 1e-12:
        return None
    return 10 * np.log10(voice_power / music_power)


def verify_aac(path: Path, bitrate: str, target_lufs: float, true_peak_dbtp: float,
               tolerance: float, oversample: int) -> tuple[float, float]:
    """Encode and decode AAC again; check limits against that PCM."""
    with tempfile.TemporaryDirectory(prefix="voice-mix-aac-") as directory:
        encoded = Path(directory) / "master.m4a"
        decoded = Path(directory) / "master-decoded.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-c:a", "aac", "-b:a", bitrate, str(encoded)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(encoded), "-c:a", "pcm_f32le", str(decoded)], check=True)
        audio, sample_rate = sf.read(decoded, always_2d=True)
    loudness, peak = master_metrics(audio, sample_rate, oversample)
    if abs(loudness - target_lufs) > tolerance or peak > true_peak_dbtp + 0.05:
        raise RuntimeError(f"AAC master outside target: {loudness:.2f} LUFS, {peak:.2f} dBTP")
    return loudness, peak


def caption_chunks(words: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    if not words:
        return []
    chunks: list[list[dict[str, Any]]] = []
    cursor = 0
    while cursor < len(words):
        end = cursor
        while end < len(words) and len(" ".join(word["t"] for word in words[cursor : end + 1])) <= max_chars:
            end += 1
        end = max(cursor + 1, end)
        candidates = list(range(cursor + 1, end + 1))
        punctuated = [candidate for candidate in candidates if words[candidate - 1]["t"].rstrip().endswith((".", ",", ";", ":", "?", "!"))]
        if punctuated:
            end = punctuated[-1]
        elif words[end - 1]["t"].lower().strip(".,;:?!") in LINK_WORDS and end - cursor > 1:
            end -= 1
        chunks.append(words[cursor:end])
        cursor = end
    return chunks


def build_captions(lines: list[dict[str, Any]], fps: float, max_chars: int, pad_before_s: float, pad_after_s: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    captions: list[dict[str, Any]] = []
    all_marks: list[dict[str, Any]] = []
    for line in lines:
        timed = [{"t": mark.get("word", mark.get("t", "")),
                  "start_s": line["start"] + float(mark.get("start", mark.get("start_s", 0))),
                  "end_s": line["start"] + float(mark.get("end", mark.get("end_s", 0)))}
                 for mark in line["words"]]
        all_marks.extend({"line": line["id"], "word": mark["t"], "start": mark["start_s"], "end": mark["end_s"],
                          "start_frame": round(mark["start_s"] * fps), "end_frame": round(mark["end_s"] * fps)} for mark in timed)
        if line.get("hide_subtitle", False):
            continue
        for index, chunk in enumerate(caption_chunks(timed, max_chars), 1):
            captions.append({
                "id": f"{line['id']}-{index}", "text": " ".join(word["t"] for word in chunk),
                "start_s": max(0, chunk[0]["start_s"] - pad_before_s), "end_s": chunk[-1]["end_s"] + pad_after_s,
                "start_frame": round(max(0, chunk[0]["start_s"] - pad_before_s) * fps),
                "end_frame": round((chunk[-1]["end_s"] + pad_after_s) * fps),
            })
    for current, following in zip(captions, captions[1:]):
        current["end_s"] = min(current["end_s"], following["start_s"])
        current["end_frame"] = min(current["end_frame"], following["start_frame"] - 1)
    return captions, all_marks


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    whole_seconds, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def write_srt(path: Path, captions: list[dict[str, Any]]) -> None:
    blocks = [f"{index}\n{srt_time(item['start_s'])} --> {srt_time(item['end_s'])}\n{item['text']}" for index, item in enumerate(captions, 1)]
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--script", type=Path, required=True)
    result.add_argument("--cues", type=Path, required=True)
    result.add_argument("--audio-dir", type=Path, required=True)
    result.add_argument("--music", type=Path)
    result.add_argument("--effects", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--voice-only", type=Path, required=True)
    result.add_argument("--placement", type=Path, required=True)
    result.add_argument("--srt", type=Path, required=True)
    result.add_argument("--words", type=Path, required=True)
    result.add_argument("--sample-rate", type=int, default=48000)
    result.add_argument("--lead-s", type=float, default=0.08)
    result.add_argument("--gap-s", type=float, default=0.4)
    result.add_argument("--max-shift-s", type=float, default=1.0)
    result.add_argument("--end-margin-s", type=float, default=0.2)
    result.add_argument("--duck-low-db", type=float, default=2.0)
    result.add_argument("--duck-mid-db", type=float, default=7.0)
    result.add_argument("--duck-high-db", type=float, default=3.0)
    result.add_argument("--low-cut-hz", type=float, default=250)
    result.add_argument("--high-cut-hz", type=float, default=5000)
    result.add_argument("--attack-ms", type=float, default=120)
    result.add_argument("--release-ms", type=float, default=600)
    result.add_argument("--bridge-s", type=float, default=1.5)
    result.add_argument("--shelf-hz", type=float, default=120)
    result.add_argument("--shelf-db", type=float, default=0.0, help="optional shelf; use -6 for excessive sub-bass")
    result.add_argument("--fx-duck-db", type=float, default=3.0)
    result.add_argument("--target-lufs", type=float, default=-14.0)
    result.add_argument("--wav-peak-dbtp", type=float, default=-1.5, help="ceiling before AAC")
    result.add_argument("--true-peak-dbtp", type=float, default=-1.0, help="ceiling measured after AAC")
    result.add_argument("--peak-oversample", type=int, default=4)
    result.add_argument("--lufs-tolerance", type=float, default=0.5)
    result.add_argument("--aac-bitrate", default="192k")
    result.add_argument("--skip-aac-verification", action="store_true", help="skip the local AAC gate (quick diagnostics only)")
    result.add_argument("--subtitle-max-chars", type=int, default=42)
    result.add_argument("--subtitle-before-ms", type=float, default=50)
    result.add_argument("--subtitle-after-ms", type=float, default=300)
    result.add_argument("--voice-band-low-hz", type=float, default=1000)
    result.add_argument("--voice-band-high-hz", type=float, default=4000)
    result.add_argument("--min-voice-over-db", type=float, default=8.0)
    return result


def main() -> None:
    args = parser().parse_args()
    script = json.loads(args.script.resolve().read_text(encoding="utf-8"))
    cues = json.loads(args.cues.resolve().read_text(encoding="utf-8"))
    lines = place_lines(script, cues, args.audio_dir.resolve(), args.sample_rate, args.lead_s, args.gap_s, args.max_shift_s, args.end_margin_s)
    total = round(float(cues["total"]) / float(cues["fps"]) * args.sample_rate)
    voice = np.zeros((total, 2))
    for line in tqdm(lines, desc="Placing voice", unit="line"):
        begin = round(line["start"] * args.sample_rate)
        voice[begin : begin + len(line["audio"])] += line["audio"]
    music_raw = read_audio(args.music.resolve(), args.sample_rate, allow_resample=True) if args.music else None
    effects_raw = read_audio(args.effects.resolve(), args.sample_rate, allow_resample=True) if args.effects else None
    spans = merge_spans([(line["start"], line["end"]) for line in lines], args.bridge_s)
    gate = smooth_gate(total, spans, args.sample_rate, args.attack_ms / 1000, args.release_ms / 1000)
    music = low_shelf(fit(music_raw, total), args.sample_rate, args.shelf_hz, args.shelf_db)
    music = band_duck(music, gate, args.sample_rate, args.low_cut_hz, args.high_cut_hz, args.duck_low_db, args.duck_mid_db, args.duck_high_db)
    effects = fit(effects_raw, total) * np.power(10, -abs(args.fx_duck_db) * gate / 20)[:, None]
    mixed, bed_gain = master_preserving_voice(
        voice, music + effects, args.sample_rate, args.target_lufs, args.wav_peak_dbtp,
        args.peak_oversample, args.lufs_tolerance,
    )
    voice_margin = voice_over_music_db(voice, music * bed_gain, gate, args.sample_rate,
                                       args.voice_band_low_hz, args.voice_band_high_hz)
    if voice_margin is not None and voice_margin < args.min_voice_over_db:
        raise RuntimeError(
            f"insufficient voice over music in {args.voice_band_low_hz:.0f}-{args.voice_band_high_hz:.0f} Hz: "
            f"{voice_margin:.2f} dB (minimum {args.min_voice_over_db:.2f} dB)"
        )
    captions, marks = build_captions(lines, float(cues["fps"]), args.subtitle_max_chars, args.subtitle_before_ms / 1000, args.subtitle_after_ms / 1000)
    outputs = [args.output, args.voice_only, args.placement, args.srt, args.words]
    for output in outputs:
        output.resolve().parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output.resolve(), mixed.astype(np.float32), args.sample_rate, subtype="PCM_24")
    sf.write(args.voice_only.resolve(), voice.astype(np.float32), args.sample_rate, subtype="FLOAT")
    placement = [{"id": line["id"], "start_s": round(line["start"], 4), "end_s": round(line["end"], 4), "shift_s": round(line["start"] - line["nominal"], 4)} for line in lines]
    args.placement.resolve().write_text(json.dumps(placement, ensure_ascii=False, indent=2), encoding="utf-8")
    args.words.resolve().write_text(json.dumps({"fps": cues["fps"], "words": marks}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(args.srt.resolve(), captions)
    written, written_rate = sf.read(args.output.resolve(), always_2d=True)
    written_lufs, written_peak = master_metrics(written, written_rate, args.peak_oversample)
    if abs(written_lufs - args.target_lufs) > args.lufs_tolerance or written_peak > args.wav_peak_dbtp + 0.05:
        raise RuntimeError(
            f"master outside target after writing WAV: {written_lufs:.2f} LUFS, {written_peak:.2f} dBTP"
        )
    if not args.skip_aac_verification:
        aac_lufs, aac_peak = verify_aac(args.output.resolve(), args.aac_bitrate, args.target_lufs,
                                        args.true_peak_dbtp, args.lufs_tolerance, args.peak_oversample)
        print(f"AAC verified: {aac_lufs:.2f} LUFS, {aac_peak:.2f} dBTP")
    print(f"bed: {20 * np.log10(max(bed_gain, 1e-12)):.2f} dB; voice preserved without gain")
    print("voice over music: no measurable energy in the band" if voice_margin is None else
          f"voice over music: {voice_margin:.2f} dB in {args.voice_band_low_hz:.0f}-{args.voice_band_high_hz:.0f} Hz")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
