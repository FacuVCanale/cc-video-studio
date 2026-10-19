# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2,<3", "soundfile>=0.13,<1"]
# ///
"""Place recorded WAV effects on Cinetic's absolute-frame events."""
import argparse
import glob
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def paths(entry, base):
    return sorted({Path(p).resolve() for pattern in entry["files"]
                   for p in glob.glob(str(base / pattern))})


def peak_index(audio, sr, window_ms):
    power = np.mean(audio.astype(np.float64) ** 2, axis=1)
    window = max(1, round(sr * window_ms / 1000))
    envelope = np.convolve(power, np.ones(window) / window, mode="same")
    return int(np.argmax(envelope))


def near_word(seconds, words, margin):
    return any(w["start"] - margin <= seconds <= w["end"] + margin for w in words)


def normalize_words(raw_words):
    if isinstance(raw_words, dict):
        raw_words = raw_words["words"]
    return [{"start": float(w.get("start", w.get("start_s"))),
             "end": float(w.get("end", w.get("end_s"))),
             "word": w.get("word", w.get("t", ""))} for w in raw_words]


def place(cues, mapping, base, words, sr, tail, peak_ms, margin, atten_db, variation_db, pan_variation):
    fps, total = float(cues["fps"]), int(cues["total"])
    events = cues["events"]
    if fps <= 0 or total <= 0 or tail < 0:
        raise ValueError("invalid fps, total, or tail")
    missing = []
    library = {}
    for i, event in enumerate(events):
        kind = event["kind"]
        files = paths(mapping[kind], base) if kind in mapping else []
        if not files:
            missing.append(f"{i}: {kind}: no files")
        elif not 2 <= len(files) <= 3:
            missing.append(f"{i}: {kind}: 2 or 3 WAV variants are required")
        if not 0 <= event["f"] < total:
            missing.append(f"{i}: frame outside the film")
        library[kind] = files
    if missing:
        raise ValueError("events without sound: " + "; ".join(missing))
    output = np.zeros((round((total / fps + tail) * sr), 2), dtype=np.float32)
    report = []
    started = time.monotonic()
    for i, event in enumerate(events):
        kind = event["kind"]
        files = library[kind]
        wanted = event.get("variant")
        if wanted:
            matching = [p for p in files if p.stem == wanted or p.name == wanted]
            if not matching:
                raise ValueError(f"{kind}: variant {wanted} does not exist")
            path = matching[0]
        else:
            path = files[sum(e["kind"] == kind for e in events[:i]) % len(files)]
        if path not in library:
            audio, original_sr = sf.read(path, dtype="float32", always_2d=True)
            if original_sr != sr:
                raise ValueError(f"{path}: expected {sr} Hz (re-encode first)")
            if len(audio) == 0 or audio.shape[1] not in (1, 2):
                raise ValueError(f"{path}: empty or multichannel WAV")
            if audio.shape[1] == 1:
                audio = np.repeat(audio, 2, axis=1)
            library[path] = (audio, peak_index(audio, sr, peak_ms))
        audio, peak = library[path]
        seconds = float(event["f"]) / fps
        start = round(seconds * sr) - peak
        if start < 0:
            raise ValueError(f"{kind} at frame {event['f']}: peak requires audio before zero")
        if start >= len(output):
            raise ValueError(f"{kind} at frame {event['f']}: outside the stem")
        collision = near_word(seconds, words, margin)
        attenuation = 10 ** (atten_db / 20) if collision else 1.0
        # Deterministic small alternation gives repeats variation without random state.
        occurrence = sum(e["kind"] == kind for e in events[:i])
        gain_db = ((occurrence % 3) - 1) * variation_db
        gain = float(mapping[kind].get("gain", 0.2)) * float(event.get("weight", 1)) * 10 ** (gain_db / 20) * attenuation
        pan = float(event.get("pan", mapping[kind].get("pan", 0))) + ((occurrence % 3) - 1) * pan_variation
        pan = float(np.clip(pan, -1, 1))
        channels = np.array([math.cos((pan + 1) * math.pi / 4), math.sin((pan + 1) * math.pi / 4)]) * math.sqrt(2)
        stop = min(len(output), start + len(audio))
        output[start:stop] += audio[:stop-start] * channels * gain
        report.append({"id": event.get("id", str(i)), "kind": kind, "frame": event["f"],
                       "file": str(path), "start_sample": start, "peak_sample": start + peak,
                       "gain": gain, "pan": pan, "word_collision": collision})
        elapsed = time.monotonic() - started
        eta = elapsed / (i + 1) * (len(events) - i - 1)
        filled = round(20 * (i + 1) / len(events))
        print(f"\rEffects [{'#' * filled}{'.' * (20-filled)}] {i+1}/{len(events)} ETA {eta:.1f}s", end="", file=sys.stderr, flush=True)
    if events:
        print(file=sys.stderr)
    return output, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cues", type=Path, required=True)
    p.add_argument("--map", type=Path, required=True, help="JSON kind -> {files: [WAV glob], gain?, pan?}")
    p.add_argument("--words", type=Path, help="JSON array {start,end,word} in seconds")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sr", type=int, default=48000)
    p.add_argument("--tail", type=float, default=1.0)
    p.add_argument("--peak-ms", type=float, default=20.0)
    p.add_argument("--distance-ms", type=float, default=80.0)
    p.add_argument("--attenuation-db", type=float, default=-10.0)
    p.add_argument("--variation-db", type=float, default=0.5)
    p.add_argument("--pan-variation", type=float, default=0.04)
    a = p.parse_args()
    cues = json.loads(a.cues.read_text())
    mapping = json.loads(a.map.read_text())
    raw_words = json.loads(a.words.read_text()) if a.words else []
    words = normalize_words(raw_words)
    output, report = place(cues, mapping, a.map.resolve().parent, words, a.sr, a.tail,
                           a.peak_ms, a.distance_ms / 1000, a.attenuation_db,
                           a.variation_db, a.pan_variation)
    dest = a.output.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    sf.write(dest, output, a.sr, subtype="FLOAT")
    dest.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(report)}/{len(cues['events'])} events: {dest}")


if __name__ == "__main__":
    main()
