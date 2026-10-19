"""earshot is optional: these tests run when it is available (EARSHOT_PYTHON or the uv cache) and skip otherwise.

The first two do not need earshot: the pinned commit matches in the wrapper and listen.py, and the disabled wrapper
exits with code 3 so the workflow can continue without it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "video-studio" / "scripts"
WRAPPER = SCRIPTS / "earshot.sh"
SR = 48_000


def run_earshot(*args: str, **env: str) -> subprocess.CompletedProcess:
    # UV_OFFLINE: without network access, uvx uses only its cache; if earshot is absent, the test skips.
    full_env = {**os.environ, "UV_OFFLINE": "1", **env}
    return subprocess.run(["bash", str(WRAPPER), *args], capture_output=True, text=True, env=full_env, timeout=600)


@pytest.fixture(scope="module")
def earshot_ok() -> None:
    if os.environ.get("EARSHOT_PYTHON") == "none":
        pytest.skip("earshot disabled (EARSHOT_PYTHON=none)")
    probe = run_earshot("sfx", "list", "--kind", "whoosh")
    if probe.returncode != 0:
        pytest.skip(f"earshot unavailable: {(probe.stderr or probe.stdout).strip().splitlines()[-1:]}")


def test_same_pinned_commit_in_wrapper_and_listen():
    wrapper = re.search(r'EARSHOT_REF:-([0-9a-f]{40})', WRAPPER.read_text())
    listen = re.search(r'earshot @ git\+https://github\.com/Leonxlnx/earshot@([0-9a-f]{40})',
                       (SCRIPTS / "listen.py").read_text())
    assert wrapper and listen, "pinned commit missing from earshot.sh or listen.py"
    assert wrapper.group(1) == listen.group(1)


def test_wrapper_switched_off_exits_3():
    r = subprocess.run(["bash", str(WRAPPER), "doctor"], capture_output=True, text=True,
                       env={**os.environ, "EARSHOT_PYTHON": "none"})
    assert r.returncode == 3
    assert "disabled" in r.stderr


def test_sfx_render_lands_duration_and_apex(earshot_ok, tmp_path):
    out = tmp_path / "w.wav"
    r = run_earshot("sfx", "render", "whoosh_fast/crisp", "-o", str(out), "--dur", "0.8", "--apex", "0.32")
    assert r.returncode == 0, r.stderr
    y, sr = sf.read(out)
    mono = np.abs(y).max(axis=1) if y.ndim == 2 else np.abs(y)
    assert abs(len(mono) / sr - 0.8) < 1e-3
    # The strongest moment (5 ms windows) lands within 10 ms of the requested time.
    win = int(0.005 * sr)
    energy = np.convolve(mono ** 2, np.ones(win), mode="same")
    assert abs(energy.argmax() / sr - 0.32) < 0.010


def test_sfx_check_hears_a_click_and_passes_a_clean_tone(earshot_ok, tmp_path):
    t = np.arange(SR) / SR
    # A decaying hit (the shape of an effect), and the same signal with a one-sample click in the tail.
    envelope = np.minimum(1, t / 0.002) * np.exp(-t / 0.12)
    clean = (0.5 * np.sin(2 * np.pi * 660 * t) * envelope).astype(np.float32)
    clicked = clean.copy()
    clicked[int(0.6 * SR)] = 0.6
    sf.write(tmp_path / "clean.wav", clean, SR)
    sf.write(tmp_path / "click.wav", clicked, SR)
    ok = run_earshot("sfx", "check", str(tmp_path / "clean.wav"))
    bad = run_earshot("sfx", "check", str(tmp_path / "click.wav"))
    assert "click" not in ok.stdout
    assert "click" in bad.stdout
