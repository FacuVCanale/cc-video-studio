#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2.0"]
# ///
"""Vote frame by frame among complete renders of the same film and choose by majority.

An intermittently faulty rasterizer (tiles from one layer drawn from another frame) leaves each fault in a
different render and frame: two renders of the same code match everywhere except there. This script compares
renders block by block and, where they differ, chooses the render that agrees with the most others for each frame.
With two renders there is no majority: --tiebreak adds one vote (another render, or an already voted backup with
the same code in that span); the tiebreak votes but contributes no frames. A frame with no matching pair or a tie
remains unresolved (choose it by eye or render again), and the script exits with code 1.

Two frames match when the worst --block-pixel analysis block differs by less than --block-limit gray levels.
With --out-video, build the voted video using --encode (without audio; add it again afterward).

usage: uv run vote_frames.py /ABS/a.mp4 /ABS/b.mp4 [/ABS/c.mp4 ...] --plan /ABS/out/vote.json
       [--tiebreak /ABS/backup.mp4] [--out-video /ABS/out/voted.mp4] [--scale 480:270 --block 15 --block-limit 5]
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

import numpy as np


def probe(video: Path) -> tuple[int, int, str, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=width,height,r_frame_rate,nb_read_packets", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"]), s["r_frame_rate"], int(s["nb_read_packets"])


def reader(video: Path, scale: str | None, pix: str) -> subprocess.Popen:
    vf = [f"scale={scale}:flags=area"] if scale else []
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-vf", ",".join(vf + [f"format={pix}"]), "-fps_mode", "passthrough",
           "-f", "rawvideo", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE)


def worst_block(a: np.ndarray, b: np.ndarray, block: int) -> float:
    d = np.abs(a.astype(np.float32) - b.astype(np.float32))
    by, bx = d.shape[0] // block, d.shape[1] // block
    return float(d[:by * block, :bx * block].reshape(by, block, bx, block).mean(axis=(1, 3)).max())


def vote(frames: list[np.ndarray], tiebreak: np.ndarray | None, block: int, limit: float) -> dict:
    """Choose the render index with the most agreements. frames: one grayscale frame per render."""
    voters = frames + ([tiebreak] if tiebreak is not None else [])
    k = len(frames)
    dist = np.zeros((len(voters), len(voters)))
    for i in range(len(voters)):
        for j in range(i + 1, len(voters)):
            dist[i, j] = dist[j, i] = worst_block(voters[i], voters[j], block)
    if all(dist[i, j] < limit for i in range(k) for j in range(i + 1, k)):
        return {"status": "agree", "choice": 0}
    agree = dist < limit
    votes = [int(agree[i].sum()) - 1 for i in range(k)]
    best = max(votes)
    if best == 0:
        return {"status": "unresolved", "choice": 0, "votes": votes, "worst": round(float(dist[:k, :k].max()), 2)}
    top = [i for i in range(k) if votes[i] == best]
    choice = min(top, key=lambda i: dist[i].sum())
    united = all(agree[i, j] for i in top for j in top)
    return {"status": "voted" if united else "tie", "choice": choice, "votes": votes,
            "worst": round(float(dist[:k, :k].max()), 2)}


def spans(frames: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for f in frames:
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("renders", nargs="+", type=Path, help="two or more complete renders of the same code")
    ap.add_argument("--tiebreak", type=Path, help="extra render that only votes (breaks ties with two renders)")
    ap.add_argument("--plan", type=Path, required=True, help="JSON report with the choice for each frame")
    ap.add_argument("--out-video", type=Path, help="optional: build the voted video without audio")
    ap.add_argument("--encode", default="-c:v libx264 -preset slow -crf 10 -pix_fmt yuv420p",
                    help="encoding arguments for --out-video")
    ap.add_argument("--scale", default="480:270", help="comparison resolution W:H")
    ap.add_argument("--block", type=int, default=15, help="block side in comparison pixels")
    ap.add_argument("--block-limit", type=float, default=5.0, help="gray levels for two blocks to match")
    a = ap.parse_args()
    if len(a.renders) < 2:
        raise SystemExit("at least two renders are required")
    paths = [p.resolve() for p in a.renders]
    tb = a.tiebreak.resolve() if a.tiebreak else None
    infos = [probe(p) for p in paths + ([tb] if tb else [])]
    if len({(w, h, n) for w, h, _, n in infos}) != 1:
        raise SystemExit("renders do not have the same size and frame count: " +
                         ", ".join(f"{w}x{h}/{n}" for w, h, _, n in infos))
    full_w, full_h, rate, total = infos[0]
    w, h = map(int, a.scale.split(":"))
    procs = [reader(p, a.scale, "gray") for p in paths]
    tproc = reader(tb, a.scale, "gray") if tb else None
    choices, rows, t0 = [], [], time.time()
    for f in range(total):
        frames = [np.frombuffer(p.stdout.read(w * h), np.uint8).reshape(h, w) for p in procs]
        tframe = np.frombuffer(tproc.stdout.read(w * h), np.uint8).reshape(h, w) if tproc else None
        res = vote(frames, tframe, a.block, a.block_limit)
        choices.append(res["choice"])
        if res["status"] != "agree":
            rows.append({"frame": f, **res})
        if (f + 1) % 60 == 0:
            el = time.time() - t0
            print(f"\rvote_frames [{f + 1}/{total}] elapsed {el:.0f}s ETA {el / (f + 1) * (total - f - 1):.0f}s",
                  end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    for p in procs + ([tproc] if tproc else []):
        p.wait()
    unresolved = [r["frame"] for r in rows if r["status"] in ("unresolved", "tie")]
    plan = {"renders": [str(p) for p in paths], "tiebreak": str(tb) if tb else None, "frames": total,
            "params": {"scale": a.scale, "block": a.block, "block_limit": a.block_limit},
            "disputed_spans": spans([r["frame"] for r in rows]), "unresolved": unresolved, "disputed": rows}
    out = a.plan.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)} disputed frames, {len(unresolved)} unresolved (including ties), out of {total}")
    if a.out_video:
        target = a.out_video.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
                                f"{full_w}x{full_h}", "-r", rate, "-i", "-", *shlex.split(a.encode), str(target)],
                               stdin=subprocess.PIPE)
        full = [reader(p, None, "rgb24") for p in paths]
        size = full_w * full_h * 3
        for f in range(total):
            bufs = [p.stdout.read(size) for p in full]
            enc.stdin.write(bufs[choices[f]])
        enc.stdin.close()
        for p in full:
            p.wait()
        if enc.wait():
            raise SystemExit("ffmpeg could not encode the voted video")
        print(f"voted video: {target}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
