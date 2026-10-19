import importlib.util
import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/video-studio/scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_score_aligns_peak_and_attenuates_near_word(tmp_path):
    score = module("score_events")
    sr = 1000
    sample = np.zeros(200, dtype=np.float32)
    sample[50:70] = 1
    sf.write(tmp_path / "click.wav", sample, sr, subtype="FLOAT")
    sf.write(tmp_path / "click-2.wav", sample, sr, subtype="FLOAT")
    cues = {"fps": 100, "total": 200, "events": [
        {"f": 50, "kind": "click"}, {"f": 100, "kind": "click"}]}
    mapping = {"click": {"files": ["click*.wav"], "gain": 0.5}}
    output, report = score.place(cues, mapping, tmp_path, [{"start": 0.98, "end": 1.02}],
                                 sr, 0.1, 10, 0.08, -10, 0, 0)
    assert len(report) == 2
    assert report[0]["file"] != report[1]["file"]
    assert abs(report[0]["peak_sample"] - 500) <= 10
    assert abs(int(np.argmax(np.abs(output[:800, 0]))) - 500) <= 10
    assert report[1]["word_collision"]
    assert np.max(output[950:1050]) == pytest.approx(np.max(output[450:550]) * 10 ** (-10 / 20), rel=0.01)
    assert score.normalize_words({"fps": 100, "words": [{"t": "hello", "start_s": 0.98, "end_s": 1.02}]}) == [
        {"start": 0.98, "end": 1.02, "word": "hello"}]


def test_score_rejects_unmapped_event(tmp_path):
    score = module("score_events")
    with pytest.raises(ValueError, match="no files"):
        score.place({"fps": 60, "total": 60, "events": [{"f": 1, "kind": "snap"}]},
                    {}, tmp_path, [], 48000, 1, 20, 0.08, -10, 0.5, 0.04)


def test_fal_cap_and_fake_transport(tmp_path, monkeypatch):
    fal = module("fal_run")
    fal_dir = tmp_path / "fal"
    fal_dir.mkdir()
    (fal_dir / "budget.json").write_text(json.dumps({"cap_usd": 0.01, "approved_by": "user", "date": "2026-10-14"}))
    item = {"id": "one", "endpoint": "fal-ai/elevenlabs/tts/eleven-v3",
            "input": {"text": "Hello", "timestamps": True}, "out": str(tmp_path / "one.wav")}
    monkeypatch.setenv("FAL_KEY", "secret-should-not-appear")
    calls = []

    def fake(url, key, data=None):
        assert key == "secret-should-not-appear"
        calls.append(url)
        if data:
            return {"status_url": "https://queue.fal.run/status", "response_url": "https://queue.fal.run/result"}
        if url.endswith("status"):
            return {"status": "COMPLETED"}
        return {"audio": {"url": "https://example.com/audio.wav"}}

    sample = io.BytesIO()
    sf.write(sample, np.zeros(480, dtype=np.float32), 48000, format="WAV", subtype="PCM_16")
    invalid = tmp_path / "invalid.part"
    invalid.write_bytes(b"RIFFfake")
    with pytest.raises(ValueError, match="unreadable"):
        fal.validate_audio(invalid, tmp_path / "invalid.wav")
    assert fal.execute([item], fal_dir, transport=fake, downloader=lambda url: sample.getvalue()) == 1
    assert len(calls) == 3
    assert (fal_dir / "spending.jsonl").exists()
    assert fal.execute([item], fal_dir, transport=fake, downloader=lambda url: b"bad") == 0
    assert len(calls) == 3
    (tmp_path / "one.wav").unlink()
    with pytest.raises(ValueError, match="reconcile"):
        fal.execute([item], fal_dir, transport=fake)
    (fal_dir / "budget.json").write_text(json.dumps({"cap_usd": 0, "approved_by": "user", "date": "2026-10-14"}))
    item = dict(item, id="two", out=str(tmp_path / "two.wav"))
    with pytest.raises(ValueError, match="exceeds balance"):
        fal.execute([item], fal_dir, transport=fake)
    assert len(calls) == 3


def test_fal_estimates_music_before_payment():
    fal = module("fal_run")
    assert fal.cost({"id": "theme", "endpoint": "fal-ai/minimax-music/v2.6", "input": {"prompt": "instrumental"}}) == 0.15
    assert fal.cost({"id": "theme2", "endpoint": "fal-ai/elevenlabs/music",
                     "input": {"prompt": "instrumental", "music_length_ms": 61000}}) == 1.6
    with pytest.raises(ValueError, match="duration is required"):
        fal.cost({"id": "theme3", "endpoint": "fal-ai/elevenlabs/music", "input": {"prompt": "instrumental"}})
