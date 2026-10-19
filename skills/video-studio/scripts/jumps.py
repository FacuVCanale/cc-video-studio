#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "Pillow"]
# ///
"""Detect one-frame visual jumps in an MP4 and write a cue-labelled report."""
import argparse, json, math, subprocess, sys, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument("video",type=Path)
    ap.add_argument("--cues",type=Path); ap.add_argument("--out",type=Path,default=Path("out/jumps.json")); ap.add_argument("--sheet",type=Path,default=Path("out/jumps-sheet.jpg")); ap.add_argument("--start",type=float,default=0); ap.add_argument("--end",type=float); ap.add_argument("--scale",default="480:270"); ap.add_argument("--factor",type=float,default=3); ap.add_argument("--min-diff",type=float,default=2); ap.add_argument("--window",type=int,default=6); a=ap.parse_args()
    video=a.video.resolve(); probe=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=r_frame_rate","-of","default=nw=1:nk=1",str(video)],capture_output=True,text=True,check=True).stdout.strip(); n,d=probe.split("/"); fps=float(n)/float(d)
    cmd=["ffmpeg","-v","error","-ss",str(a.start),"-i",str(video)];
    if a.end is not None: cmd += ["-t",str(max(0,a.end-a.start))]
    cmd += ["-vf",f"scale={a.scale},format=gray","-f","rawvideo","-"]; proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    w,h=map(int,a.scale.split(":")); chunk=w*h; previous=None; diff=[]; started=time.time(); total_hint=None
    durprobe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(video)],capture_output=True,text=True).stdout.strip()
    if durprobe:
        total_hint=max(1,round((float(durprobe)-(a.start or 0))*fps))
    while True:
        block=proc.stdout.read(chunk)
        if not block: break
        if len(block)!=chunk: break
        current=np.frombuffer(block,dtype=np.uint8).copy()
        if previous is not None: diff.append(float(np.abs(current.astype(np.int16)-previous.astype(np.int16)).mean()))
        previous=current
        if len(diff)%max(1,int(fps))==0:
            elapsed=time.time()-started; done=len(diff)+1; eta=elapsed*(total_hint-done)/done if total_hint and done<total_hint else 0
            filled=min(20,round(20*done/total_hint)) if total_hint else 0
            print(f"\rjumps decode [{'#'*filled}{'.'*(20-filled)}] {done}/{total_hint or '?'} ETA {eta:.1f}s",end="",file=sys.stderr,flush=True)
    print(file=sys.stderr)
    _,err=proc.communicate()
    if proc.returncode: raise SystemExit(err.decode(errors="replace"))
    if previous is None: raise SystemExit("could not decode frames")
    diff=np.asarray(diff); cue={}
    if a.cues: cue={int(v):k for k,v in json.loads(a.cues.resolve().read_text()).get("cue",{}).items() if isinstance(v,(int,float))}
    jumps=[]
    started=time.time()
    for i,val in enumerate(diff):
        if i % max(1,len(diff)//20)==0:
            elapsed=time.time()-started; eta=elapsed*(len(diff)-i)/max(1,i) if i else 0
            filled=round(20*i/max(1,len(diff)))
            print(f"\rjumps [{'#'*filled}{'.'*(20-filled)}] {i}/{len(diff)} ETA {eta:.1f}s",end="",file=sys.stderr,flush=True)
        lo=max(0,i-a.window); hi=min(len(diff),i+a.window+1); base=np.delete(diff[lo:hi],i-lo); med=float(np.median(base)) if len(base) else 0; frame=round(a.start*fps)+i+1
        if val>a.factor*med+1 and val>a.min_diff:
            near=[name for f,name in cue.items() if abs(f-frame)<=3]; jumps.append({"frame":frame,"seconds":frame/fps,"change":round(float(val),3),"baseline":round(med,3),"nearCues":near,"declaredCut":bool(near)})
    print(file=sys.stderr)
    report={"video":str(video),"fps":fps,"frames":len(diff)+1,"jumps":jumps}; out=a.out.resolve(); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    if not jumps and a.sheet.exists(): a.sheet.unlink()
    if jumps:
        ims=[]
        for j in jumps:
            from io import BytesIO
            raw_frame=subprocess.run(["ffmpeg","-v","error","-ss",str(j["seconds"]),"-i",str(video),"-frames:v","1","-vf",f"scale={a.scale}","-f","image2pipe","-vcodec","png","-"],capture_output=True,check=True).stdout
            im=Image.open(BytesIO(raw_frame)).convert("RGB"); im.thumbnail((480,270)); c=Image.new("RGB",(480,300),"white"); c.paste(im,(0,0)); ImageDraw.Draw(c).text((8,276),f"f={j['frame']} {j['change']:.1f}/{j['baseline']:.1f} cue={','.join(j['nearCues']) or '-'}",fill="black"); ims.append(c)
        sheet=Image.new("RGB",(1440,300*math.ceil(len(ims)/3)),"white")
        for i,im in enumerate(ims): sheet.paste(im,((i%3)*480,(i//3)*300))
        p=a.sheet.resolve(); p.parent.mkdir(parents=True,exist_ok=True); sheet.save(p,quality=92)
    print(json.dumps(report,ensure_ascii=False)); return 1 if jumps else 0
if __name__=="__main__": sys.exit(main())
