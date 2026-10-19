"""Cut takes at silence, check peak edges, find stale tiles, vote renders, and clean bundles."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "video-studio" / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cut = load("silence_cut")
SR = 48_000


def take_with_gap() -> np.ndarray:
    """Line 1: 0.05-0.80 s (two words); silence 0.80-1.10 s; line 2: 1.10-1.80 s; tail to 2.0 s."""
    t = np.arange(int(2.0 * SR)) / SR
    y = 0.3 * np.sin(2 * np.pi * 220 * t)
    speech = ((t >= 0.05) & (t < 0.40)) | ((t >= 0.44) & (t < 0.80)) | ((t >= 1.10) & (t < 1.80))
    return np.where(speech, y, 0.0)


# TTS times shifted by +0.4 s: the midpoint between lines (1.35 s) falls inside the second line's speech
WORDS = [{"word": "Hello", "start": 0.45, "end": 0.80}, {"word": "world.", "start": 0.84, "end": 1.20},
         {"word": "Goodbye", "start": 1.50, "end": 1.85}, {"word": "everyone.", "start": 1.87, "end": 2.0}]
LINES = [{"id": "v1", "text": "Hello world."}, {"id": "v2", "text": "Goodbye everyone."}]


def test_cut_lands_in_the_silence_not_at_the_drifted_midpoint():
    y = take_with_gap()
    plan = cut.plan_cuts(y, SR, LINES, WORDS, head_ms=300, tail_ms=450)
    boundary = plan[0]["right"]
    assert plan[1]["left"] >= boundary
    assert 0.83 <= boundary <= 1.07 and plan[0]["cut_in_silence"]
    naive_mid = (WORDS[1]["end"] + WORDS[2]["start"]) / 2
    assert 1.10 < naive_mid < 1.80  # cutting there would clip the second line
    assert plan[1]["speech"][0] == 1.10


def test_cut_without_a_gap_warns_and_uses_the_energy_minimum():
    t = np.arange(SR) / SR
    y = 0.3 * np.sin(2 * np.pi * 220 * t) * (0.2 + 0.8 * np.abs(t - 0.5))
    words = [{"word": "a", "start": 0.0, "end": 0.45}, {"word": "b", "start": 0.55, "end": 1.0}]
    plan = cut.plan_cuts(y, SR, [{"id": "x", "text": "a"}, {"id": "y", "text": "b"}], words)
    assert plan[0]["cut_in_silence"] is False
    assert abs(plan[0]["right"] - 0.5) < 0.02


def test_words_from_characters_advance_across_repeated_phrases():
    text = "Hello world. Hello world."
    starts = [i * 0.01 for i in range(len(text))]
    ends = [(i + 1) * 0.01 for i in range(len(text))]
    words = cut.words_from_characters(text, starts, ends)
    assert [w["word"] for w in words] == ["Hello", "world.", "Hello", "world."]
    assert words[2]["start"] > words[1]["end"]


def test_silence_cut_cli_writes_quiet_edges(tmp_path: Path):
    audio = tmp_path / "take.wav"
    sf.write(audio, take_with_gap().astype(np.float32), SR)
    (tmp_path / "words.json").write_text(json.dumps(WORDS))
    (tmp_path / "lines.json").write_text(json.dumps(LINES))
    out = tmp_path / "lines"
    p = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "silence_cut.py"), "--audio", str(audio),
                        "--words", str(tmp_path / "words.json"), "--lines", str(tmp_path / "lines.json"),
                        "--output-dir", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert json.loads((out / "report.json").read_text())["lines"][0]["cut_in_silence"] is True
    edges = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "voice_edges.py"), str(out), "--measure", "peak",
                            "--window-ms", "20", "--threshold-dbfs", "-30"], capture_output=True, text=True)
    assert edges.returncode == 0, edges.stdout


def test_voice_edges_peak_catches_a_click_that_rms_misses(tmp_path: Path):
    y = np.zeros(SR // 2, dtype=np.float32)
    y[5] = 0.5  # a one-sample click at the edge
    sf.write(tmp_path / "click.wav", y, SR)
    base = ["uv", "run", "--script", str(SCRIPTS / "voice_edges.py"), str(tmp_path), "--window-ms", "30",
            "--threshold-dbfs", "-30"]
    assert subprocess.run(base, capture_output=True, text=True).returncode == 0
    assert subprocess.run(base + ["--measure", "peak"], capture_output=True, text=True).returncode == 1


def ffv1(path: Path, frames: list[np.ndarray], rate: int = 10) -> None:
    h, w = frames[0].shape[:2]
    pix, out_pix = ("gray", "gray") if frames[0].ndim == 2 else ("rgb24", "gbrp")
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{w}x{h}",
                          "-r", str(rate), "-i", "-", "-c:v", "ffv1", "-pix_fmt", out_pix, str(path)],
                         stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f, dtype=np.uint8).tobytes())
    p.stdin.close()
    assert p.wait() == 0


def test_ghost_tiles_flags_a_faint_stale_tile_and_ignores_motion(tmp_path: Path):
    frames = []
    for i in range(12):
        f = np.full((270, 480), 100, np.uint8)
        x = 20 + 4 * i  # a moving square (its edges also change between f-1 and f+1)
        f[200:240, x:x + 40] = 250
        if i == 5:
            f[40:120, 100:300] += 12  # faint content tile only in frame 5
            f[60:100, 140:260] += 8
        frames.append(f)
    video = tmp_path / "g.mkv"
    ffv1(video, frames)
    out = tmp_path / "ghost.json"
    p = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "ghost_tiles.py"), str(video), "--out", str(out),
                        "--csv", str(tmp_path / "g.csv")], capture_output=True, text=True)
    assert p.returncode == 1, p.stderr
    flagged = json.loads(out.read_text())["flagged"]
    assert [r["frame"] for r in flagged] == [5]
    x0, y0, x1, y1 = flagged[0]["box"]
    assert x0 <= 100 and y0 <= 40 and x1 >= 296 and y1 >= 112 and y1 < 200


def scene(i: int) -> np.ndarray:
    f = np.zeros((90, 160, 3), np.uint8)
    f[:, :, 1] = 60
    f[30:60, (i * 9) % 120:(i * 9) % 120 + 30] = (200, 180, 40)
    return f


def with_tile(f: np.ndarray) -> np.ndarray:
    g = f.copy()
    g[5:25, 100:150] = (240, 240, 240)
    return g


def test_vote_frames_picks_the_majority_and_uses_the_tiebreak(tmp_path: Path):
    clean = [scene(i) for i in range(10)]
    a = [with_tile(f) if i == 3 else f for i, f in enumerate(clean)]
    b = [with_tile(f) if i == 6 else f for i, f in enumerate(clean)]
    c = [with_tile(f) if i == 8 else f for i, f in enumerate(clean)]
    paths = {}
    for name, frames in {"a": a, "b": b, "c": c, "ref": clean}.items():
        paths[name] = tmp_path / f"{name}.mkv"
        ffv1(paths[name], frames)
    script = ["uv", "run", "--script", str(SCRIPTS / "vote_frames.py"), "--scale", "160:90", "--block", "10"]
    plan_path, voted = tmp_path / "vote.json", tmp_path / "voted.mkv"
    p = subprocess.run(script + [str(paths["a"]), str(paths["b"]), str(paths["c"]), "--plan", str(plan_path),
                                 "--out-video", str(voted), "--encode", "-c:v ffv1 -pix_fmt gbrp"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    plan = json.loads(plan_path.read_text())
    choice = {r["frame"]: r["choice"] for r in plan["disputed"]}
    assert choice[3] in (1, 2) and choice[6] in (0, 2) and choice[8] in (0, 1)
    assert all(r["status"] == "voted" for r in plan["disputed"])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(voted), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    out = np.frombuffer(raw, np.uint8).reshape(10, 90, 160, 3)
    assert np.array_equal(out, np.stack(clean))
    # With two renders, the tiebreak decides; without it, differences have no majority.
    two = subprocess.run(script + [str(paths["a"]), str(paths["b"]), "--plan", str(plan_path)], capture_output=True)
    assert two.returncode == 1 and json.loads(plan_path.read_text())["unresolved"] == [3, 6]
    tie = subprocess.run(script + [str(paths["a"]), str(paths["b"]), "--tiebreak", str(paths["ref"]), "--plan",
                                   str(plan_path)], capture_output=True)
    assert tie.returncode == 0
    choice = {r["frame"]: r["choice"] for r in json.loads(plan_path.read_text())["disputed"]}
    assert choice == {3: 1, 6: 0}


def test_vote_frames_tiebreak_matching_both_does_not_create_a_majority(tmp_path: Path):
    paths = {}
    for name, level in {"a": 0, "b": 8, "tiebreak": 4}.items():
        paths[name] = tmp_path / f"{name}.mkv"
        ffv1(paths[name], [np.full((90, 160), level, np.uint8)])
    plan_path = tmp_path / "vote.json"
    voted_path = tmp_path / "voted.mkv"
    p = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "vote_frames.py"), str(paths["a"]),
                        str(paths["b"]), "--tiebreak", str(paths["tiebreak"]), "--plan", str(plan_path),
                        "--scale", "160:90", "--block", "10", "--block-limit", "5",
                        "--out-video", str(voted_path), "--encode", "-c:v ffv1 -pix_fmt gbrp"],
                       capture_output=True, text=True)
    assert p.returncode == 1, p.stderr
    assert voted_path.exists()
    plan = json.loads(plan_path.read_text())
    assert plan["unresolved"] == [0]
    assert plan["disputed"][0]["status"] == "tie"


def test_clean_render_tmp_only_removes_old_matching_dirs(tmp_path: Path):
    old, fresh, other = (tmp_path / "remotion-webpack-bundle-old", tmp_path / "remotion-webpack-bundle-new",
                         tmp_path / "keep-me")
    for d in (old, fresh, other):
        d.mkdir()
        (d / "x").write_text("x")
    past = time.time() - 10 * 3600
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    script = ["bash", str(SCRIPTS / "clean_render_tmp.sh"), "--dir", str(tmp_path), "--older-than", "6",
              "--process-regex", "no-such-process-xyz-123"]
    dry = subprocess.run(script + ["--dry-run"], capture_output=True, text=True, check=True)
    assert str(old.name) in dry.stdout and old.exists()
    subprocess.run(script, capture_output=True, text=True, check=True)
    assert not old.exists() and fresh.exists() and other.exists()
