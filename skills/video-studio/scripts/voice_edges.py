#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2.0", "soundfile>=0.12", "tqdm>=4.66"]
# ///
"""Check that the beginning and end of every voice WAV are quiet enough.

A loud edge means the cut fell inside a word: a bitten attack at the start, or the next word leaking in at
the end; in the mix it sounds like a click at the splice. --measure rms catches a sustained edge; --measure
peak (with a short --window-ms, e.g. 20 ms and -30 dBFS) also catches a single click that RMS averages away.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from tqdm import tqdm


def rms_dbfs(samples: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(np.asarray(samples, dtype=np.float64)))))
    return -120.0 if rms <= 1e-12 else float(20 * np.log10(rms))


def peak_dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(np.asarray(samples, dtype=np.float64)))) if len(samples) else 0.0
    return -120.0 if peak <= 1e-12 else float(20 * np.log10(peak))


def inspect(path: Path, window_ms: float, threshold_dbfs: float, measure: str = "rms") -> dict[str, object]:
    audio, sample_rate = sf.read(path, always_2d=True, dtype="float64")
    count = max(1, round(sample_rate * window_ms / 1000))
    if len(audio) < count:
        raise ValueError(f"{path}: shorter than the {window_ms:g} ms edge window")
    level = peak_dbfs if measure == "peak" else rms_dbfs
    start = level(audio[:count])
    end = level(audio[-count:])
    return {
        "path": str(path),
        "sample_rate": sample_rate,
        "window_ms": window_ms,
        "threshold_dbfs": threshold_dbfs,
        "measure": measure,
        "start_dbfs": round(start, 3),
        "end_dbfs": round(end, 3),
        "passes": start < threshold_dbfs and end < threshold_dbfs,
    }


def _files(inputs: list[Path], recursive: bool) -> list[Path]:
    found: set[Path] = set()
    for raw in inputs:
        path = raw.expanduser().resolve()
        if path.is_file() and path.suffix.lower() == ".wav":
            found.add(path)
        elif path.is_dir():
            found.update(item.resolve() for item in path.glob("**/*.wav" if recursive else "*.wav"))
        else:
            raise ValueError(f"not a WAV file or directory: {path}")
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="absolute WAV paths or directories")
    parser.add_argument("--window-ms", type=float, default=30.0)
    parser.add_argument("--threshold-dbfs", type=float, default=-45.0)
    parser.add_argument("--measure", choices=("rms", "peak"), default="rms",
                        help="rms of each edge window, or its sample peak (catches single clicks)")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output", type=Path, help="optional absolute JSON report path")
    args = parser.parse_args()
    if args.window_ms <= 0:
        raise SystemExit("window-ms must be positive")
    try:
        files = _files(args.inputs, args.recursive)
        if not files:
            raise ValueError("no WAV files found")
        rows = [inspect(path, args.window_ms, args.threshold_dbfs, args.measure)
                for path in tqdm(files, desc="Edges", unit="WAV", file=sys.stderr)]
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    report = {"checked": len(rows), "failed": sum(not row["passes"] for row in rows), "files": rows}
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
