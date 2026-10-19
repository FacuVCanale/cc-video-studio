#!/bin/bash
# uv run below supplies Pillow for pixel-level verification.
set -e
PROJECT=""; COMMAND=""; OUT=""; ENTRY=""; COMPOSITION=""; TOTAL_FRAMES=0
while [ "$#" -gt 0 ]; do case "$1" in --help|-h) echo "usage: $0 --project ABS --entry ABS --composition ID --total-frames N [--out ABS]"; echo "test: --project ABS --render-command 'command {out} {concurrency}' [--total-frames N] [--out ABS]"; exit 0;; --project) PROJECT="$2"; shift 2;; --render-command) COMMAND="$2"; shift 2;; --entry) ENTRY="$2"; shift 2;; --composition) COMPOSITION="$2"; shift 2;; --total-frames) TOTAL_FRAMES="$2"; shift 2;; --out) OUT="$2"; shift 2;; *) echo "usage: $0 --project ABS --entry entry.tsx --composition ID --total-frames N [--render-command ...] [--out ABS]" >&2; exit 2;; esac; done
[ -n "$PROJECT" ] && { [ -n "$COMMAND" ] || { [ -n "$ENTRY" ] && [ -n "$COMPOSITION" ] && [ "$TOTAL_FRAMES" -gt 0 ]; }; } || exit 2
PROJECT=$(cd "$PROJECT" && pwd)
[ -n "$OUT" ] || OUT="$PROJECT/out/determinism"
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
if [ -z "$COMMAND" ]; then
  case "$ENTRY" in /*) ;; *) echo "--entry requires an absolute path" >&2; exit 2;; esac
  [ -f "$ENTRY" ] || { echo "--entry does not exist" >&2; exit 2; }
fi
run_one() {
  c="$1"; d="$OUT/c$c"; rm -rf "$d"; mkdir -p "$d"
  if [ -n "$COMMAND" ]; then
    cmd=$(printf '%s' "$COMMAND" | sed "s|{concurrency}|$c|g; s|{out}|$d|g")
    (cd "$PROJECT" && eval "$cmd") &
  else
    (cd "$PROJECT" && npx remotion render "$ENTRY" "$COMPOSITION" "$d" --sequence --image-format=png --concurrency="$c") &
  fi
  render_pid=$!; started=$(date +%s)
  while kill -0 "$render_pid" 2>/dev/null; do
    if [ "$TOTAL_FRAMES" -gt 0 ]; then
      count=$(find "$d" -type f -name '*.png' | wc -l | tr -d ' ')
      [ "$count" -le "$TOTAL_FRAMES" ] || count="$TOTAL_FRAMES"
      elapsed=$(( $(date +%s) - started ))
      eta='?'; [ "$count" -eq 0 ] || eta=$(( elapsed * (TOTAL_FRAMES - count) / count ))
      filled=$(( 20 * count / TOTAL_FRAMES ))
      bar=$(printf '%*s' "$filled" '' | tr ' ' '#')
      printf '\rdeterminism c%s [%-20s] %s/%s ETA %ss' "$c" "$bar" "$count" "$TOTAL_FRAMES" "$eta" >&2
    fi
    sleep 1
  done
  wait "$render_pid"
  printf '\n' >&2
}
run_one 1; run_one 4
uv run --with Pillow --script - "$OUT/c1" "$OUT/c4" "$OUT/result.json" "$TOTAL_FRAMES" <<'PY'
import hashlib,json,os,sys
from PIL import Image, ImageChops
def fs(d): return sorted(x for x in os.listdir(d) if x.endswith('.png'))
a,b,o,total=sys.argv[1:]; fa,fb=fs(a),fs(b); dif=[]; total=int(total)
for f in sorted(set(fa)|set(fb)):
    ha=hashlib.sha256(open(os.path.join(a,f),'rb').read()).hexdigest() if f in fa else None; hb=hashlib.sha256(open(os.path.join(b,f),'rb').read()).hexdigest() if f in fb else None
    if ha!=hb: dif.append(f)
if not fa or not fb: raise SystemExit('no PNG files were generated')
for f in sorted(set(fa)&set(fb)):
    try:
        if ImageChops.difference(Image.open(os.path.join(a,f)).convert('RGBA'),Image.open(os.path.join(b,f)).convert('RGBA')).getbbox() is not None and f not in dif: dif.append(f)
    except Exception: dif.append(f)
r={'framesCompared':len(set(fa)&set(fb)),'expectedFrames':total or None,'differentFrames':dif,'passed':not dif and fa==fb and (not total or len(fa)==total)}; open(o,'w').write(json.dumps(r,indent=2)+'\n'); print(json.dumps(r)); raise SystemExit(0 if r['passed'] else 1)
PY
