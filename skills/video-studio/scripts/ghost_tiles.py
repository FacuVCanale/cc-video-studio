#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2.0"]
# ///
"""Detect faint ghost tiles: content from another frame that appears in a single frame.

This complements jumps.py, which examines whole-frame changes and misses a faint content tile (text or a panel from
another moment at low opacity). For each frame f, it examines small blocks. A block is suspicious when neighbors
f-1 and f+1 nearly match each other (difference < --still), but f differs from both (> --diff). An old tile fills
many adjacent blocks in a still scene; a moving object also changes between f-1 and f+1, so it does not count,
except for a small, very fast element that covers a background in one frame that its neighbors do not touch. The
report therefore includes each frame's box for inspection at full frame size.
Exit with code 1 if any frame has --min-blocks or more.

usage: uv run ghost_tiles.py /ABS/film.mp4 --out /ABS/out/ghost.json [--csv /ABS/out/ghost.csv]
       [--scale 480:270 --block 8 --diff 2.0 --still 0.8 --min-blocks 4]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np


def probe(video: Path) -> tuple[int, int, float, int | None]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,nb_frames", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    frames = int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else None
    return int(s["width"]), int(s["height"]), float(num) / float(den), frames


def suspicious_blocks(a0: np.ndarray, a1: np.ndarray, a2: np.ndarray, block: int, diff: float,
                      still: float) -> np.ndarray:
    """Mask blocks where a1 differs from both neighbors and the neighbors match."""
    by, bx = a1.shape[0] // block, a1.shape[1] // block

    def blk(d: np.ndarray) -> np.ndarray:
        return d[:by * block, :bx * block].reshape(by, block, bx, block).mean(axis=(1, 3))

    d01, d12, d02 = blk(np.abs(a1 - a0)), blk(np.abs(a2 - a1)), blk(np.abs(a2 - a0))
    return (np.minimum(d01, d12) > diff) & (d02 < still)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="JSON report")
    ap.add_argument("--csv", type=Path, help="optional: one row per frame (frame,n,area,x0,y0,x1,y1)")
    ap.add_argument("--scale", default="480:270", help="analysis resolution W:H")
    ap.add_argument("--block", type=int, default=8, help="block side in analysis pixels")
    ap.add_argument("--diff", type=float, default=2.0, help="minimum frame difference from each neighbor")
    ap.add_argument("--still", type=float, default=0.8, help="maximum difference between the two neighbors")
    ap.add_argument("--min-blocks", type=int, default=4, help="suspicious blocks required to flag a frame")
    ap.add_argument("--start-frame", type=int, default=0, help="first frame to decode")
    ap.add_argument("--frames", type=int, help="number of frames to analyze from --start-frame")
    a = ap.parse_args()
    video = a.video.resolve()
    full_w, full_h, fps, total = probe(video)
    w, h = map(int, a.scale.split(":"))
    sx, sy = full_w / w, full_h / h
    vf = f"select=gte(n\\,{a.start_frame}),scale={w}:{h}:flags=area,format=gray"
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-vf", vf, "-fps_mode", "passthrough"]
    if a.frames:
        cmd += ["-frames:v", str(a.frames)]
    cmd += ["-f", "rawvideo", "-"]
    expected = a.frames or (total - a.start_frame if total else None)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size, hist, flagged, rows = w * h, [], [], []
    i, t0 = 0, time.time()
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        hist.append(np.frombuffer(buf, np.uint8).reshape(h, w).astype(np.float32))
        hist = hist[-3:]
        if len(hist) == 3:
            frame = a.start_frame + i - 1
            sus = suspicious_blocks(*hist, a.block, a.diff, a.still)
            n = int(sus.sum())
            row = {"frame": frame, "seconds": round(frame / fps, 3), "n": n}
            if n:
                ys, xs = np.nonzero(sus)
                box = [int(xs.min() * a.block * sx), int(ys.min() * a.block * sy),
                       int((xs.max() + 1) * a.block * sx), int((ys.max() + 1) * a.block * sy)]
                row.update(box=box, area=(box[2] - box[0]) * (box[3] - box[1]))
            rows.append(row)
            if n >= a.min_blocks:
                flagged.append(row)
        i += 1
        if i % max(1, round(fps)) == 0:
            el = time.time() - t0
            eta = f"{el / i * (expected - i):.0f}s" if expected else "?"
            print(f"\rghost_tiles [{i}/{expected or '?'}] elapsed {el:.0f}s ETA {eta}", end="", file=sys.stderr,
                  flush=True)
    print(file=sys.stderr)
    if proc.wait():
        raise SystemExit("ffmpeg could not decode the video")
    if i < 3:
        raise SystemExit("at least three frames are required")
    report = {"video": str(video), "fps": fps, "analysed": i, "params": {k: getattr(a, k) for k in
              ("scale", "block", "diff", "still", "min_blocks")}, "flagged": flagged}
    out = a.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.csv:
        lines = ["frame,n,area,x0,y0,x1,y1"]
        for r in rows:
            b = r.get("box", ["", "", "", ""])
            lines.append(f"{r['frame']},{r['n']},{r.get('area', 0)},{b[0]},{b[1]},{b[2]},{b[3]}")
        a.csv.resolve().parent.mkdir(parents=True, exist_ok=True)
        a.csv.resolve().write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(flagged)} frames with {a.min_blocks}+ suspicious blocks out of {i}")
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
