#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2.0", "soundfile>=0.12"]
# ///
"""Cut a TTS take into lines at actual silence between words.

TTS timings (character or word alignment) may be offset by half a second, so the cut does not fall at the midpoint
between one line and the next. Between the last word of one line and the first word of the next (plus --drift
tolerance on each side), the script finds the longest low-energy gap below --db and cuts at the quietest frame in
that gap that leaves --edge-ms of silence on each side. If there is no --min-gap-ms gap, it cuts at the energy
minimum in the window and warns. Each line keeps the speech between its two cuts (with --head-ms / --tail-ms
margins) and a --fade-ms fade at each edge.

usage: uv run silence_cut.py --audio /ABS/take.wav --words /ABS/words.json --lines /ABS/lines.json \
        --output-dir /ABS/lines [--db -45 --drift 0.3 --min-gap-ms 60 --edge-ms 30 --fade-ms 10]

  words.json: [{"word": "hello", "start": 0.12, "end": 0.40}, ...] in take seconds
  lines.json: [{"id": "v1", "text": "Hello world."}, ...] in order (accepts "tts" instead of "text")
Writes <id>.wav (at the take sample rate) and report.json with the cut, gap, and whether it landed in silence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

HOP_S = 0.01


def plain_word(token: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFD", token.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ]+", "", plain)


def energy_db(audio: np.ndarray, sample_rate: int, hop_s: float = HOP_S) -> np.ndarray:
    """RMS level in dBFS per hop_s window (mono)."""
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    hop = max(1, int(round(hop_s * sample_rate)))
    pad = (-len(mono)) % hop
    frames = np.pad(np.asarray(mono, dtype=np.float64), (0, pad)).reshape(-1, hop)
    return 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12))


def silence_cut(level: np.ndarray, window: tuple[float, float], mid: float, *, db: float, min_gap_ms: float,
                edge_ms: float, hop_s: float = HOP_S) -> dict[str, Any]:
    """Choose the cut between two lines within ``window`` (seconds).

    Return {"cut_s", "gap_s", "silence"}: the longest gap below ``db`` (breaking ties by proximity to ``mid``),
    and its quietest frame at least ``edge_ms`` from speech; without a sufficient gap, the energy minimum.
    """
    q0 = max(0, int(window[0] / hop_s))
    q1 = min(len(level), int(np.ceil(window[1] / hop_s)))
    if q1 <= q0:
        q = min(max(int(mid / hop_s), 0), len(level) - 1)
        return {"cut_s": (q + 0.5) * hop_s, "gap_s": 0.0, "silence": False}
    quiet = level[q0:q1] <= db
    runs, start = [], None
    for i, flag in enumerate(quiet):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((q0 + start, q0 + i))
            start = None
    if start is not None:
        runs.append((q0 + start, q1))
    best = max(runs, key=lambda r: (r[1] - r[0], -abs((r[0] + r[1]) / 2 * hop_s - mid)), default=None)
    if best and (best[1] - best[0]) * hop_s * 1000 >= min_gap_ms:
        margin = int(round(edge_ms / 1000 / hop_s))
        lo, hi = best[0] + margin, best[1] - margin
        q = lo + int(np.argmin(level[lo:hi])) if hi > lo else (best[0] + best[1]) // 2
        return {"cut_s": (q + 0.5) * hop_s, "gap_s": round((best[1] - best[0]) * hop_s, 3), "silence": True}
    q = q0 + int(np.argmin(level[q0:q1]))
    return {"cut_s": (q + 0.5) * hop_s, "gap_s": 0.0, "silence": False}


def line_word_ranges(lines: list[dict[str, Any]], timed: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Indices [a, b) of each line's words in ``timed`` (script order)."""
    heard = [plain_word(item["word"]) for item in timed]
    ranges, cursor = [], 0
    for line in lines:
        expected = [w for w in (plain_word(t) for t in str(line.get("tts", line.get("text", ""))).split()) if w]
        found = next((i for i in range(cursor, len(heard) - len(expected) + 1)
                      if heard[i:i + len(expected)] == expected), None)
        if found is None or not expected:
            raise ValueError(f"line {line['id']} did not align with the word timings")
        ranges.append((found, found + len(expected)))
        cursor = found + len(expected)
    return ranges


def words_from_characters(text: str, starts: list[float], ends: list[float]) -> list[dict[str, Any]]:
    """Convert character alignment to timed words (discard standalone punctuation)."""
    words = []
    for match in re.finditer(r"\S+", text):
        token = match.group(0)
        if plain_word(token):
            words.append({"word": token, "start": float(starts[match.start()]), "end": float(ends[match.end() - 1])})
    return words


def plan_cuts(audio: np.ndarray, sample_rate: int, lines: list[dict[str, Any]], timed: list[dict[str, Any]], *,
              db: float = -45.0, drift: float = 0.3, min_gap_ms: float = 60.0, edge_ms: float = 30.0,
              head_ms: float = 300.0, tail_ms: float = 450.0) -> list[dict[str, Any]]:
    """Calculate each line's [left, right] take segment and contained speech."""
    timed = [w for w in timed if plain_word(w["word"])]
    ranges = line_word_ranges(lines, timed)
    duration = len(audio) / sample_rate
    level = energy_db(audio, sample_rate)
    cuts, gaps = [0.0], []
    for k in range(len(lines) - 1):
        (a, b), (c, _) = ranges[k], ranges[k + 1]
        window = (max(timed[b - 1]["start"] - drift, 0.0), min(timed[c]["end"] + drift, duration))
        mid = (timed[b - 1]["end"] + timed[c]["start"]) / 2
        choice = silence_cut(level, window, mid, db=db, min_gap_ms=min_gap_ms, edge_ms=edge_ms)
        if not choice["silence"]:
            print(f"warning: between {lines[k]['id']} and {lines[k + 1]['id']} there is no {min_gap_ms:g} ms gap below "
                  f"{db:g} dBFS; cutting at the energy minimum ({choice['cut_s']:.2f} s)", file=sys.stderr)
        cuts.append(choice["cut_s"])
        gaps.append(choice)
    cuts.append(duration)
    plan = []
    for k, (line, (a, b)) in enumerate(zip(lines, ranges)):
        b0, b1 = cuts[k], cuts[k + 1]
        q0, q1 = int(b0 / HOP_S), min(int(np.ceil(b1 / HOP_S)), len(level))
        loud = np.nonzero(level[q0:q1] > db)[0]
        on = (q0 + loud[0]) * HOP_S if len(loud) else timed[a]["start"]
        off = (q0 + loud[-1] + 1) * HOP_S if len(loud) else timed[b - 1]["end"]
        left, right = max(b0, on - head_ms / 1000), min(b1, off + tail_ms / 1000)
        plan.append({"id": line["id"], "left": round(left, 4), "right": round(right, 4),
                     "speech": [round(on, 4), round(off, 4)], "words": [a, b],
                     "gap_after_s": gaps[k]["gap_s"] if k < len(gaps) else None,
                     "cut_in_silence": gaps[k]["silence"] if k < len(gaps) else None})
    return plan


def apply_fades(segment: np.ndarray, sample_rate: int, fade_in_ms: float, fade_out_ms: float) -> np.ndarray:
    segment = segment.copy()
    fade_in = min(len(segment) // 2, round(fade_in_ms * sample_rate / 1000))
    fade_out = min(len(segment) // 2, round(fade_out_ms * sample_rate / 1000))
    shape = (slice(None),) + (None,) * (segment.ndim - 1)
    if fade_in:
        segment[:fade_in] *= np.linspace(0, 1, fade_in)[shape]
    if fade_out:
        segment[-fade_out:] *= np.linspace(1, 0, fade_out)[shape]
    return segment


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", type=Path, required=True, help="complete take (WAV/FLAC; decode MP3 first)")
    ap.add_argument("--words", type=Path, help="take word timings")
    ap.add_argument("--alignment", type=Path,
                    help="character alignment {characters, character_start_times_seconds, character_end_times_seconds}")
    ap.add_argument("--lines", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--db", type=float, default=-45.0, help="speech threshold (dBFS in 10 ms windows)")
    ap.add_argument("--drift", type=float, default=0.3, help="alignment tolerance when finding the gap (s)")
    ap.add_argument("--min-gap-ms", type=float, default=60.0, help="minimum gap for cutting at silence")
    ap.add_argument("--edge-ms", type=float, default=30.0, help="minimum silence between the cut and speech")
    ap.add_argument("--head-ms", type=float, default=300.0, help="maximum margin before speech")
    ap.add_argument("--tail-ms", type=float, default=450.0, help="maximum margin after speech")
    ap.add_argument("--fade-ms", type=float, default=10.0, help="fade at each cut edge")
    a = ap.parse_args()
    if (a.words is None) == (a.alignment is None):
        raise SystemExit("pass exactly one of --words or --alignment")
    audio, sample_rate = sf.read(a.audio.resolve(), always_2d=True, dtype="float64")
    lines = json.loads(a.lines.read_text(encoding="utf-8"))
    if a.words:
        timed = json.loads(a.words.read_text(encoding="utf-8"))
        timed = timed["words"] if isinstance(timed, dict) else timed
    else:
        al = json.loads(a.alignment.read_text(encoding="utf-8"))
        al = al.get("alignment", al)
        timed = words_from_characters("".join(al["characters"]), al["character_start_times_seconds"],
                                      al["character_end_times_seconds"])
    plan = plan_cuts(audio, sample_rate, lines, timed, db=a.db, drift=a.drift, min_gap_ms=a.min_gap_ms,
                     edge_ms=a.edge_ms, head_ms=a.head_ms, tail_ms=a.tail_ms)
    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for row in plan:
        seg = audio[round(row["left"] * sample_rate):round(row["right"] * sample_rate)]
        sf.write(out / f"{row['id']}.wav", apply_fades(seg, sample_rate, a.fade_ms, a.fade_ms).astype(np.float32),
                 sample_rate, subtype="FLOAT")
        print(f"{row['id']:<8} {row['left']:7.2f}–{row['right']:7.2f} s · gap after {row['gap_after_s']}")
    (out / "report.json").write_text(json.dumps({"audio": str(a.audio.resolve()), "lines": plan}, indent=1,
                                                ensure_ascii=False) + "\n", encoding="utf-8")
    return 1 if any(r["cut_in_silence"] is False for r in plan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
