import json, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; J=ROOT/'skills/video-studio/scripts/jumps.py'; D=ROOT/'skills/video-studio/scripts/determinism.sh'

def test_jumps_synthetic(tmp_path):
    video=tmp_path/'x.mp4'; out=tmp_path/'j.json'
    cues=tmp_path/'cues.json'; cues.write_text(json.dumps({'fps':10,'total':10,'cue':{'cut':5},'events':[]}))
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=black:s=64x36:r=10:d=1','-vf','drawbox=x=0:y=0:w=64:h=36:color=white:t=fill:enable=\'eq(n,5)\'','-c:v','ffv1',str(video)],check=True)
    p=subprocess.run(['uv','run','--script',str(J),str(video),'--out',str(out),'--sheet',str(tmp_path/'sheet.jpg'),'--cues',str(cues)],capture_output=True,text=True)
    assert p.returncode==1; assert json.loads(out.read_text())['jumps']
    assert any(j['declaredCut'] and 'cut' in j['nearCues'] for j in json.loads(out.read_text())['jumps'])

def test_determinism_fake_renderer(tmp_path):
    project=tmp_path/'p'; project.mkdir(); fake=project/'render.py'; fake.write_text("import sys; from pathlib import Path; from PIL import Image; d=Path(sys.argv[1]); d.mkdir(exist_ok=True); [Image.new('RGB',(2,2),(10,10,10)).save(d/f'f{i:03}.png') for i in range(3)]")
    out=tmp_path/'out'; cmd='uv run --with Pillow render.py {out}'
    p=subprocess.run([str(D),'--project',str(project),'--render-command',cmd,'--out',str(out)],capture_output=True,text=True)
    assert p.returncode==0; assert json.loads((out/'result.json').read_text())['passed']
    short=subprocess.run([str(D),'--project',str(project),'--render-command',cmd,'--out',str(out),'--total-frames','4'],capture_output=True,text=True)
    assert short.returncode==1
    assert json.loads((out/'result.json').read_text())['expectedFrames']==4


def test_determinism_rejects_different_frames(tmp_path):
    project=tmp_path/'p'; project.mkdir()
    fake=project/'render.py'
    fake.write_text("import sys; from pathlib import Path; from PIL import Image; d=Path(sys.argv[1]); d.mkdir(exist_ok=True); color=(255,0,0) if sys.argv[2]=='4' else (0,0,0); Image.new('RGB',(2,2),color).save(d/'f000.png')")
    out=tmp_path/'out'
    p=subprocess.run([str(D),'--project',str(project),'--render-command','uv run --with Pillow render.py {out} {concurrency}','--out',str(out)],capture_output=True,text=True)
    assert p.returncode==1
    result=json.loads((out/'result.json').read_text())
    assert result['differentFrames']==['f000.png']
    assert result['passed'] is False
