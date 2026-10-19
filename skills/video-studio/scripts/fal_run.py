# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Estimate and run capped fal audio jobs from a JSONL batch."""
import argparse
import datetime as dt
import fcntl
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


# Confirm against official model pages before spending; prices are estimates.
PRICES = {
    "fal-ai/elevenlabs/tts/eleven-v3": ("chars", 0.10 / 1000),
    "fal-ai/elevenlabs/sound-effects/v2": ("seconds", 0.002),
    "fal-ai/minimax-music/v2.6": ("track", 0.15),
    "fal-ai/elevenlabs/music": ("minute", 0.80),
}
PRICE_DATE = "2026-10-14"


def cost(item):
    endpoint = item["endpoint"]
    if endpoint not in PRICES:
        raise ValueError(f"endpoint without a verified price: {endpoint}")
    unit, rate = PRICES[endpoint]
    data = item["input"]
    if unit == "chars":
        quantity = len(data["text"])
    elif unit == "seconds":
        quantity = float(data["duration_seconds"])
    elif unit == "track":
        quantity = 1
    else:
        plan = data.get("composition_plan")
        milliseconds = sum(float(section["duration_ms"]) for section in plan["sections"]) if plan else data.get("music_length_ms")
        if milliseconds is None:
            raise ValueError(f"{item['id']}: duration is required to estimate music")
        quantity = math.ceil(float(milliseconds) / 60000)
    if quantity <= 0:
        raise ValueError(f"{item['id']}: invalid billable quantity")
    return math.ceil(quantity * rate * 10000) / 10000


def read_batch(path):
    items = []
    seen = set()
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not all(k in item for k in ("id", "endpoint", "input", "out")):
            raise ValueError(f"line {line_no}: id, endpoint, input, or out is missing")
        if item["id"] in seen or not str(item["id"]).replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"line {line_no}: duplicate or invalid id")
        seen.add(item["id"])
        item["out"] = str(Path(item["out"]).resolve())
        cost(item)
        items.append(item)
    return items


def spent(ledger):
    if not ledger.exists():
        return 0.0
    return sum(float(json.loads(line)["estimated_usd"]) for line in ledger.read_text().splitlines() if line.strip())


def reserved_ids(ledger):
    return {json.loads(line)["id"] for line in ledger.read_text().splitlines() if line.strip()} if ledger.exists() else set()


def cap(path):
    if not path.exists():
        raise ValueError(f"written budget is missing: {path}")
    data = json.loads(path.read_text())
    if not data.get("approved_by") or not data.get("date"):
        raise ValueError("budget.json requires approved_by and date")
    value = data.get("cap_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid cap_usd")
    return float(value)


def key_from_environment():
    key = os.environ.get("FAL_KEY")
    if not key and os.environ.get("FAL_KEY_CMD"):
        command = os.environ["FAL_KEY_CMD"]
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError("FAL_KEY_CMD failed")
        key = result.stdout.strip()
    if not key:
        raise ValueError("FAL_KEY or FAL_KEY_CMD is missing")
    return key


def http_json(url, key, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, payload, method="POST" if payload else "GET")
    request.add_header("Authorization", "Key " + key)
    if payload:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def validate_audio(path, output):
    """Reject a paid response that is not decodable audio of the requested type."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_name", "-of", "json", str(path)],
                           capture_output=True, text=True)
    if probe.returncode:
        raise ValueError("fal returned an unreadable audio file")
    streams = json.loads(probe.stdout).get("streams", [])
    if not streams:
        raise ValueError("fal did not return an audio stream")
    codec = streams[0]["codec_name"]
    suffix = output.suffix.lower()
    if suffix == ".mp3" and codec != "mp3":
        raise ValueError("fal returned a format other than MP3")
    if suffix == ".wav" and not codec.startswith("pcm_"):
        raise ValueError("fal returned a format other than PCM WAV")
    if suffix not in (".mp3", ".wav"):
        raise ValueError("output must be .mp3 or .wav")
    check = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                            "-map", "0:a:0", "-f", "null", "-"], capture_output=True)
    if check.returncode:
        raise ValueError("fal returned corrupt audio")


def run_item(item, key, transport=http_json, sleep=time.sleep, timeout=600, poll=3):
    submitted = transport("https://queue.fal.run/" + item["endpoint"], key, item["input"])
    status_url, response_url = submitted["status_url"], submitted["response_url"]
    if not all(url.startswith("https://queue.fal.run/") for url in (status_url, response_url)):
        raise ValueError("unexpected queue URL")
    deadline = time.monotonic() + timeout
    while True:
        status = transport(status_url, key)["status"]
        if status == "COMPLETED":
            break
        if status not in ("IN_QUEUE", "IN_PROGRESS") or time.monotonic() >= deadline:
            raise RuntimeError("fal did not complete the request")
        remaining = max(0, deadline - time.monotonic())
        print(f"\rFal {item['id']}: {status}, maximum wait remaining {remaining:.0f}s", end="", file=sys.stderr, flush=True)
        sleep(poll)
    result = transport(response_url, key)
    audio_url = result.get("audio", {}).get("url")
    if not audio_url or not audio_url.startswith("https://"):
        raise ValueError("fal did not return HTTPS audio")
    return result, audio_url


def _execute_locked(items, fal_dir, transport, downloader):
    """Injectable transport and downloader keep tests offline."""
    ledger = fal_dir / "spending.jsonl"
    logs = fal_dir / "logs"
    ceiling = cap(fal_dir / "budget.json")
    pending = [item for item in items if not Path(item["out"]).exists()]
    ambiguous = reserved_ids(ledger) & {item["id"] for item in pending}
    if ambiguous:
        raise ValueError("requests already reserved without a file; reconcile them in fal before retrying: " + ", ".join(sorted(ambiguous)))
    estimate = sum(cost(item) for item in pending)
    already = spent(ledger)
    if already + estimate > ceiling + 1e-9:
        raise ValueError(f"batch US${estimate:.4f} exceeds balance US${ceiling-already:.4f}")
    if not pending:
        return 0
    key = key_from_environment()
    fal_dir.mkdir(parents=True, exist_ok=True)
    logs.mkdir(exist_ok=True)
    download = downloader or (lambda url: urllib.request.urlopen(url, timeout=300).read())
    began = time.monotonic()
    for index, item in enumerate(pending, 1):
        # A request may be billed even if polling or download fails. Reserve first.
        with ledger.open("a") as f:
            f.write(json.dumps({"id": item["id"], "endpoint": item["endpoint"],
                                "estimated_usd": cost(item), "out": item["out"],
                                "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                "state": "reserved"}) + "\n")
        try:
            result, url = run_item(item, key, transport=transport)
            content = download(url)
            out = Path(item["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            temporary = out.with_suffix(out.suffix + ".part")
            temporary.write_bytes(content)
            validate_audio(temporary, out)
            temporary.replace(out)
            (logs / (item["id"] + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        except Exception as exc:
            # Network exceptions may contain request headers; never print their details.
            raise RuntimeError(f"request {item['id']} failed; check its status in fal") from None
        elapsed = time.monotonic() - began
        eta = elapsed / index * (len(pending) - index)
        filled = round(20 * index / len(pending))
        print(f"\rFal [{'#' * filled}{'.' * (20-filled)}] {index}/{len(pending)} ETA {eta:.0f}s", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return len(pending)


def execute(items, fal_dir, transport=http_json, downloader=None):
    """Serialize paid batches so two processes cannot spend the same balance."""
    fal_dir.mkdir(parents=True, exist_ok=True)
    with (fal_dir / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _execute_locked(items, fal_dir, transport, downloader)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path, nargs="?")
    p.add_argument("--fal-dir", type=Path, default=Path("audio/fal"))
    p.add_argument("--estimate", action="store_true")
    p.add_argument("--spent", action="store_true")
    a = p.parse_args()
    directory = a.fal_dir.resolve()
    ledger = directory / "spending.jsonl"
    if a.spent:
        print(f"spent US${spent(ledger):.4f}; budget {directory / 'budget.json'}")
        return
    if not a.batch:
        p.error("JSONL batch is missing")
    items = read_batch(a.batch.resolve())
    pending = [item for item in items if not Path(item["out"]).exists()]
    estimate = sum(cost(item) for item in pending)
    print(f"{len(pending)} pending; estimated US${estimate:.4f}; spent US${spent(ledger):.4f}; prices {PRICE_DATE}")
    if not a.estimate:
        execute(items, directory)


if __name__ == "__main__":
    main()
