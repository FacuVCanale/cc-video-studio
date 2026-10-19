from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "video-studio" / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tts = load_script("tts_takes")
qc = load_script("voice_qc")
mix = load_script("voice_mix")


def tone(path: Path, frequency: float, seconds: float, sample_rate: int = 48_000, amplitude: float = 0.2) -> None:
    time = np.arange(round(seconds * sample_rate)) / sample_rate
    sf.write(path, (amplitude * np.sin(2 * np.pi * frequency * time)).astype(np.float32), sample_rate)


def test_takes_are_two_to_four_lines_without_explicit_groups():
    lines = [{"id": f"v{i}", "text": str(i)} for i in range(7)]
    groups = tts.group_takes(lines, 2, 4)
    assert [len(group) for group in groups] == [4, 3]
    assert [line["id"] for group in groups for line in group] == [line["id"] for line in lines]
    with pytest.raises(ValueError, match="between 2 and 4"):
        tts.group_takes([{"id": "v1", "text": "one", "take": "short"}], 2, 4)


def test_fal_word_timestamps_align_and_reject_a_changed_word():
    log = {"timestamps": [{"word": "Hello", "start": 0.1, "end": 0.4}, {"word": "world", "start": 0.5, "end": 0.8}]}
    timed = tts.fal_word_timestamps(log)
    assert timed == [{"word": "Hello", "start": 0.1, "end": 0.4}, {"word": "world", "start": 0.5, "end": 0.8}]
    assert tts.line_word_ranges([{"id": "a", "text": "Hello world"}], timed) == [(0, 2)]
    with pytest.raises(ValueError, match="did not align"):
        tts.line_word_ranges([{"id": "a", "text": "Hello everyone"}], timed)


def test_prepare_fal_uses_real_mp3_extension_and_v3_schema(tmp_path: Path):
    args = type("Args", (), {
        "output_dir": tmp_path.resolve(), "batch": None, "endpoint": "fal-ai/elevenlabs/tts/eleven-v3",
        "voice": "demo", "speed": 1.0, "audio_format": "mp3",
    })()
    batch = tts.prepare_fal(args, [[{"id": "v1", "text": "Hello", "take": "t1"}]])
    item = json.loads(batch.read_text(encoding="utf-8"))
    assert item["out"].endswith("t1.mp3")
    assert set(item["input"]) == {"text", "voice", "timestamps"}


def test_qc_equivalences_and_mutated_transcript():
    score, differences = qc.compare_text("Try caf\u00e9 with hundred cases", "Test cafe with 100 cases", {"test": "try", "100": "hundred"})
    assert score == 100
    assert differences == []
    changed_score, changed = qc.compare_text("Try caf\u00e9 with hundred cases", "Try cafe with twenty cases", {})
    assert changed_score < 100
    assert changed[0]["type"] == "replace"


def test_pitch_stats_detects_tone_and_tone_gate_rejects_shift(tmp_path: Path):
    path = tmp_path / "voice.wav"
    tone(path, 180, 0.5)
    stats = qc.pitch_stats(path, min_hz=100, max_hz=260)
    assert stats["f0_median_hz"] == pytest.approx(180, abs=3)
    assert qc.tone_ok(stats, (180, 180), 10)
    assert not qc.tone_ok(stats, (120, 120), 10)
    assert not qc.tone_ok({"f0_median_hz": None, "f0_p90_hz": None}, (180, 180), 10)


def test_placement_shifts_at_most_one_second_and_rejects_overflow(tmp_path: Path):
    tone(tmp_path / "a.wav", 180, 1.0)
    tone(tmp_path / "b.wav", 180, 1.0)
    for line_id in ("a", "b"):
        (tmp_path / f"{line_id}.words.json").write_text(json.dumps([{"word": line_id, "start": 0, "end": 0.8}]))
    script = [{"id": "a", "text": "A", "anchor": "a"}, {"id": "b", "text": "B", "anchor": "b"}]
    cues = {"fps": 10, "total": 50, "cue": {"a": 0, "b": 8}}
    lines = mix.place_lines(script, cues, tmp_path, 48_000, lead_s=0, gap_s=0.4, max_shift_s=1, end_margin_s=0)
    assert lines[1]["start"] == pytest.approx(1.4)
    with pytest.raises(ValueError, match="requires a .* shift"):
        mix.place_lines(script, {**cues, "cue": {"a": 0, "b": 1}}, tmp_path, 48_000, 0, 0.4, 0.2, 0)


def test_placement_counts_total_shift_and_requires_marks(tmp_path: Path):
    tone(tmp_path / "a.wav", 180, 0.5)
    tone(tmp_path / "b.wav", 180, 0.5)
    with pytest.raises(ValueError, match="word marks are missing"):
        mix.place_lines([{"id": "a", "text": "A", "anchor": "a"}],
                        {"fps": 10, "total": 20, "cue": {"a": 0}}, tmp_path, 48_000, 0, 0.4, 1, 0)
    for line_id, take_start, take_end in (("a", 0, 0.5), ("b", 1.8, 2.3)):
        (tmp_path / f"{line_id}.words.json").write_text(json.dumps([{"word": line_id, "start": 0, "end": 0.4}]))
        (tmp_path / f"{line_id}.take.json").write_text(json.dumps({"take": "t1", "start_s": take_start, "end_s": take_end}))
    script = [{"id": "a", "text": "A", "anchor": "a"}, {"id": "b", "text": "B", "anchor": "b"}]
    with pytest.raises(ValueError, match="requires a .* shift"):
        mix.place_lines(script, {"fps": 10, "total": 30, "cue": {"a": 0, "b": 6}}, tmp_path, 48_000, 0, 0.4, 1, 0)


def test_fal_character_alignment_cuts_through_word_times(tmp_path: Path):
    source = tmp_path / "t1.wav"
    tone(source, 180, 1.0, sample_rate=24_000)
    log = {"timestamps": [{"characters": list("Hello world."), "character_start_times_seconds":
                           [0.05 + i * 0.05 for i in range(12)], "character_end_times_seconds":
                           [0.1 + i * 0.05 for i in range(12)]}]}
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "t1.json").write_text(json.dumps(log))
    args = type("Args", (), {
        "head_ms": 0, "tail_ms": 0, "fade_in_ms": 10, "fade_out_ms": 60, "output_dir": tmp_path / "out",
        "output_sample_rate": 48_000, "takes_dir": tmp_path, "logs_dir": tmp_path / "logs", "audio_format": "wav",
    })()
    tts.cut_fal(args, [[{"id": "v1", "text": "Hello world.", "take": "t1"}]])
    marks = json.loads((tmp_path / "out" / "v1.words.json").read_text())
    assert [m["word"] for m in marks] == ["Hello", "world."]


def test_ducking_reduces_mid_band_more_than_low_band():
    sample_rate = 48_000
    time = np.arange(sample_rate) / sample_rate
    low = np.sin(2 * np.pi * 100 * time)
    middle = np.sin(2 * np.pi * 1000 * time)
    source = np.stack([low + middle, low + middle], axis=1)
    ducked = mix.band_duck(source, np.ones(sample_rate), sample_rate, 250, 5000, 2, 7, 3)
    low_sos = mix.signal.butter(4, [80, 120], "bandpass", fs=sample_rate, output="sos")
    mid_sos = mix.signal.butter(4, [900, 1100], "bandpass", fs=sample_rate, output="sos")
    low_drop = 20 * np.log10(np.std(mix.signal.sosfilt(low_sos, ducked[:, 0])) / np.std(mix.signal.sosfilt(low_sos, source[:, 0])))
    mid_drop = 20 * np.log10(np.std(mix.signal.sosfilt(mid_sos, ducked[:, 0])) / np.std(mix.signal.sosfilt(mid_sos, source[:, 0])))
    assert low_drop == pytest.approx(-2, abs=0.5)
    assert mid_drop == pytest.approx(-7, abs=0.5)


def test_duck_gate_attack_and_release_land_on_voice_edges():
    gate = mix.smooth_gate(400, [(0.2, 0.3)], sample_rate=1000, attack_s=0.1, release_s=0.1)
    assert gate[99] == 0
    assert gate[100] == 0
    assert gate[150] == pytest.approx(0.5)
    assert gate[200] == 1
    assert gate[300] == 1
    assert gate[350] == pytest.approx(0.5)
    assert gate[399] == pytest.approx(0.01)


def test_subtitles_stay_under_limit_and_avoid_link_word():
    words = [{"t": token, "start_s": index * 0.2, "end_s": index * 0.2 + 0.15}
             for index, token in enumerate("A simple tool for measuring every change precisely.".split())]
    chunks = mix.caption_chunks(words, 24)
    assert all(len(" ".join(word["t"] for word in chunk)) <= 24 for chunk in chunks)
    assert all(chunk[-1]["t"].lower().strip(".,") not in mix.LINK_WORDS for chunk in chunks[:-1])


def test_voice_mix_cli_writes_audio_srt_and_word_frames(tmp_path: Path):
    audio_dir = tmp_path / "takes"
    audio_dir.mkdir()
    tone(audio_dir / "v1.wav", 180, 0.5, amplitude=0.08)
    (audio_dir / "v1.words.json").write_text(json.dumps([
        {"word": "Hello", "start": 0.05, "end": 0.2},
        {"word": "world.", "start": 0.22, "end": 0.42},
    ]), encoding="utf-8")
    script = tmp_path / "script.json"
    cues = tmp_path / "cues.json"
    script.write_text(json.dumps([{"id": "v1", "text": "Hello world.", "anchor": "start"}]), encoding="utf-8")
    cues.write_text(json.dumps({"fps": 60, "total": 180, "cue": {"start": 0}}), encoding="utf-8")
    music = tmp_path / "music.wav"
    tone(music, 90, 3.0, sample_rate=44_100, amplitude=0.02)
    outputs = {name: tmp_path / filename for name, filename in {
        "output": "mix.wav", "voice-only": "voice.wav", "placement": "placement.json",
        "srt": "voice.srt", "words": "words.json",
    }.items()}
    command = ["uv", "run", "--script", str(SCRIPTS / "voice_mix.py"), "--script", str(script.resolve()),
               "--cues", str(cues.resolve()), "--audio-dir", str(audio_dir.resolve()), "--music", str(music.resolve())]
    for flag, path in outputs.items():
        command.extend([f"--{flag}", str(path.resolve())])
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    assert "AAC verified:" in completed.stdout
    assert outputs["output"].exists() and outputs["voice-only"].exists()
    assert "Hello world." in outputs["srt"].read_text(encoding="utf-8")
    marks = json.loads(outputs["words"].read_text(encoding="utf-8"))["words"]
    assert marks[0]["word"] == "Hello"
    assert marks[0]["start_frame"] == round(marks[0]["start"] * 60)
    assert marks[1]["end_frame"] == round(marks[1]["end"] * 60)


def test_aac_gate_rejects_loudness_or_peak_mutation(tmp_path: Path):
    path = tmp_path / "too-loud.wav"
    tone(path, 1000, 1.0, amplitude=0.99)
    with pytest.raises(RuntimeError, match="AAC master outside target"):
        mix.verify_aac(path, "128k", target_lufs=-30, true_peak_dbtp=-10, tolerance=0.1, oversample=4)


def test_master_changes_only_background_and_preserves_voice_pcm():
    sample_rate = 48_000
    time = np.arange(sample_rate) / sample_rate
    voice = np.stack([0.03 * np.sin(2 * np.pi * 180 * time)] * 2, axis=1)
    bed = np.stack([0.02 * np.sin(2 * np.pi * 90 * time)] * 2, axis=1)
    mastered, gain = mix.master_preserving_voice(voice, bed, sample_rate, -14, -1.5, 4, 0.5)
    np.testing.assert_allclose(mastered - bed * gain, voice, rtol=0, atol=1e-12)


def test_tts_cut_resamples_line_to_mix_rate(tmp_path: Path):
    source = tmp_path / "take.wav"
    tone(source, 180, 0.5, sample_rate=24_000)
    args = type("Args", (), {
        "head_ms": 0, "tail_ms": 0, "fade_in_ms": 10, "fade_out_ms": 60,
        "output_dir": tmp_path, "output_sample_rate": 48_000,
    })()
    timed = [{"word": "Hello", "start": 0.05, "end": 0.4}]
    tts.cut_from_word_times(source, [{"id": "v1", "text": "Hello"}], timed, args)
    _, rate = sf.read(tmp_path / "v1.wav")
    assert rate == 48_000


def test_voice_over_music_band_ratio_detects_masking():
    sample_rate = 48_000
    time = np.arange(sample_rate) / sample_rate
    voice = np.stack([0.2 * np.sin(2 * np.pi * 1500 * time)] * 2, axis=1)
    quiet_music = np.stack([0.05 * np.sin(2 * np.pi * 1500 * time)] * 2, axis=1)
    loud_music = quiet_music * 8
    gate = np.ones(sample_rate)
    assert mix.voice_over_music_db(voice, quiet_music, gate, sample_rate, 1000, 4000) == pytest.approx(12.04, abs=0.2)
    assert mix.voice_over_music_db(voice, loud_music, gate, sample_rate, 1000, 4000) < 0


def render_is_active() -> bool:
    return subprocess.run(["pgrep", "-fl", "remotion|chrome-headless"], capture_output=True).returncode == 0


@pytest.mark.skipif(os.getenv("VIDEO_STUDIO_WHISPER_SMOKE") != "1", reason="smoke tiny opt-in")
def test_whisper_tiny_smoke_is_opt_in(tmp_path: Path):
    if render_is_active():
        pytest.skip("a heavy render is active")
    transcribe = qc.make_transcriber("tiny", "es", "auto", "auto")
    tone(tmp_path / "silence.wav", 180, 0.2, amplitude=0)
    assert isinstance(transcribe(tmp_path / "silence.wav"), str)


@pytest.mark.skipif(os.getenv("VIDEO_STUDIO_KOKORO_SMOKE") != "1", reason="smoke Kokoro opt-in")
def test_kokoro_smoke_is_opt_in():
    if render_is_active():
        pytest.skip("a heavy render is active")
    pytest.importorskip("kokoro")
