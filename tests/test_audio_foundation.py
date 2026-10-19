# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy>=2.0",
#   "pytest>=8.3",
#   "scipy>=1.13",
#   "soundfile>=0.12",
#   "tqdm>=4.66",
# ]
# ///
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import soundfile as sf


DEFAULT_SCRIPTS = Path(__file__).parents[1] / "skills" / "video-studio" / "scripts"
SCRIPTS = Path(os.environ.get("VIDEO_STUDIO_SCRIPTS_DIR", DEFAULT_SCRIPTS)).resolve()


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pulse_track(
    sample_rate: int = 8000, duration: float = 12.0, bpm: float = 120.0, accent_offset: int = 2
) -> np.ndarray:
    samples = np.zeros(round(sample_rate * duration), dtype=np.float64)
    width = round(sample_rate * 0.08)
    time = np.arange(width) / sample_rate
    kick = np.sin(2 * np.pi * 70 * time) * np.exp(-time * 35)
    for index, beat in enumerate(np.arange(0.2, duration, 60 / bpm)):
        start = round(beat * sample_rate)
        gain = 2.0 if index % 4 == accent_offset else 1.0
        samples[start : start + width] += gain * kick[: min(width, len(samples) - start)]
    samples += 0.03 * np.sin(2 * np.pi * 261.6256 * np.arange(len(samples)) / sample_rate)
    return np.column_stack((samples, samples))


def test_beats_measures_kick_grid_and_key():
    beats = load("beats")
    audio = pulse_track()
    result = beats.analyze_audio(audio, 8000, fps=60, min_bpm=90, max_bpm=150)
    assert result["tempo_bpm"] == pytest.approx(120, abs=2)
    assert result["kick_verification"]["median_deviation_ms"] < 15
    assert result["key"]["tonic"] == "C"
    assert result["beats_frames"][1] - result["beats_frames"][0] == pytest.approx(30, abs=1)
    assert result["downbeat_offset"] == 2
    assert result["downbeat_confidence"] > 0.1
    assert result["downbeats_s"][0] == pytest.approx(result["beats_s"][2])
    uniform = beats.analyze_audio(pulse_track(accent_offset=-1), 8000, fps=60, min_bpm=90, max_bpm=150)
    assert uniform["suitable_for_bar_cuts"] is False
    overridden = beats.analyze_audio(
        pulse_track(accent_offset=-1), 8000, fps=60, min_bpm=90, max_bpm=150, downbeat_offset=1
    )
    assert overridden["suitable_for_bar_cuts"] is True
    assert overridden["downbeat_source"] == "override"
    assert overridden["downbeat_offset"] == 1


def test_music_edit_places_climax_and_preserves_own_final_bar():
    load("beats")
    music_edit = load("music_edit")
    sample_rate = 1000
    bar_samples = 2000
    amplitudes = [0.1, 0.2, 0.9, 0.3, 0.18, 0.55]
    bars = [np.full((bar_samples, 2), amplitude, dtype=np.float64) for amplitude in amplitudes]
    bars[-1][:, 0] = np.linspace(0.2, 0.8, bar_samples)
    bars[-1][:, 1] = bars[-1][:, 0]
    audio = np.concatenate(bars)
    analysis = {
        "beat_s": 0.5,
        "beats_per_bar": 4,
        "downbeats_s": [float(index * 2) for index in range(len(bars))],
    }
    edited, report = music_edit.edit_music(audio, sample_rate, analysis, duration_s=10, climax_s=4, crossfade_ms=0)
    assert len(edited) == 10_000
    assert report["plan_source_bars"][2] == 2
    assert report["plan_source_bars"][-1] == 5
    assert report["ends_with_song_final_bar"] is True
    assert edited[-1, 0] == pytest.approx(0.8)
    assert edited[-bar_samples, 0] == pytest.approx(0.2)
    assert report["contains_padding_silence"] is False
    plain = music_edit.plan_bars([(i * bar_samples, (i + 1) * bar_samples) for i in range(6)], amplitudes, 5, 2)
    inserted = music_edit.plan_bars([(i * bar_samples, (i + 1) * bar_samples) for i in range(6)], amplitudes, 5, 2, [(1, 1)])
    assert inserted[1:3] == [plain[1], plain[1]]
    assert inserted[3] == plain[2]
    assert inserted[-1] == plain[-1]
    with pytest.raises(ValueError, match="integral number"):
        music_edit.edit_music(audio, sample_rate, analysis, duration_s=9.5, climax_s=4, crossfade_ms=0)
    phased = dict(analysis, downbeats_s=[0.2 + index * 2 for index in range(5)])
    bounds = music_edit._bar_bounds(phased, len(audio), sample_rate, len(audio))
    assert bounds[0][0] == 200
    assert all(left[1] <= right[0] for left, right in zip(bounds, bounds[1:]))
    three_four = dict(analysis, beats_per_bar=3)
    assert music_edit._parse_inserts([{"frame": 174, "bars": 2}], 60, three_four) == [(2, 2)]
    ambiguous = dict(analysis, suitable_for_bar_cuts=False)
    with pytest.raises(ValueError, match="downbeat phase is ambiguous"):
        music_edit.edit_music(audio, sample_rate, ambiguous, duration_s=10, climax_s=4, crossfade_ms=0)

    dc = np.ones((12_000, 2), dtype=np.float64)
    rendered = music_edit.render_plan(
        dc,
        [(index * 2000, (index + 1) * 2000) for index in range(6)],
        [0, 1, 2, 0, 5],
        10_000,
        sample_rate,
        20,
        15,
    )
    assert len(rendered) == 10_000
    assert np.min(rendered[5980:6020]) >= 0.99


def test_voice_edges_cli_reports_every_failure(tmp_path: Path):
    sample_rate = 8000
    quiet = np.zeros(sample_rate)
    good = tmp_path / "good.wav"
    sf.write(good, quiet, sample_rate, subtype="FLOAT")
    bad_audio = quiet.copy()
    bad_audio[:240] = 0.1
    bad = tmp_path / "bad.wav"
    sf.write(bad, bad_audio, sample_rate, subtype="FLOAT")
    command = ["uv", "run", str(SCRIPTS / "voice_edges.py"), str(tmp_path)]
    failed = subprocess.run(command, capture_output=True, text=True, check=False)
    report = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert report["checked"] == 2
    assert report["failed"] == 1
    assert next(row for row in report["files"] if row["path"] == str(bad))["passes"] is False
    passed = subprocess.run(command + ["--threshold-dbfs", "-10"], capture_output=True, text=True, check=False)
    assert passed.returncode == 0


import pytest  # noqa: E402  (keeps the test dependencies visible above)
