"""AI-News scenes v2: generate lower-third strips + title cards (system font),
rewrite all 16 collage scenes to use screenshot + lower-third + number chip."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

RUN = Path("/Users/zachrizzo/.video-studio/runs/run_1eb772e9")
SHARED = RUN / "assets" / "shared"
INK=(31,30,27); ACCENT=(217,119,87); MUTED=(120,117,104); PAPER=(240,238,230)

def _f(size, bold=True):
    for p,idx in ([("/System/Library/Fonts/Helvetica.ttc",1)] if bold else [("/System/Library/Fonts/Helvetica.ttc",0)]) + [
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",0),("/System/Library/Fonts/Helvetica.ttc",0)]:
        try: return ImageFont.truetype(p, size, index=idx)
        except Exception: continue
    return ImageFont.load_default()

def fit(draw, text, maxw, start, bold=True):
    s=start
    while s>28:
        f=_f(s,bold)
        if draw.textlength(text,font=f)<=maxw: return f
        s-=3
    return _f(28,bold)

# (n, headline, stat, source)
DATA=[
 (1,"NO LAUNCH, NO SPECS","0 OFFICIAL SPECS","9to5Google"),
 (2,"OPENAI'S FIRST HARDWARE","$230 KEYPAD","9to5Mac"),
 (3,"27B MODEL IN 4GB","3.9 GB, RUNS LOCAL","PrismML"),
 (4,"EVERYONE WANTS AI CHIPS","REPORTED, NOT CONFIRMED","The Information"),
 (5,"BONUSES FIRST, AI SECOND","3-DAY STRIKE","Bloomberg"),
 (6,"AI BANNED FROM LIVESTREAMS","4X LESS BRAND TRUST","Social Media Today"),
 (7,"THE AI RACE SPLITS IN TWO","29-NATION BLOC","Al Jazeera"),
 (8,"CHATGPT TALKS LIKE A HUMAN","76% PREFERRED IT","TechCrunch"),
 (9,"YOUR FACE, THEN PULLED","OPT-OUT BACKLASH","Axios"),
 (10,"8,000 JOBS, STALLED AI","~10% OF STAFF","TechCrunch"),
 (11,"BETTING BIG ON ROBOTS","$73.6M, 12TH DEAL","CNBC"),
 (12,"FIXING AI DOOM LOOPS","22.9% TO 1%","Liquid AI"),
 (13,"AI READS YOUR BRAIN","$52M RAISED","The Next Web"),
 (14,"16 NOBEL LAUREATES WARN","200+ ECONOMISTS","Al Jazeera"),
]

def gen_lowerthird(n, headline, stat, source):
    W,H=1760,300
    im=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(im)
    # translucent legibility bar + accent rule
    d.rounded_rectangle([0,16,W,H],radius=10,fill=PAPER+(214,))
    d.rectangle([0,16,W,28],fill=ACCENT)
    fh=fit(d,headline,W-60,96)
    d.text((28,54),headline,font=fh,fill=INK)
    fs=_f(44); d.text((30,206),stat,font=fs,fill=ACCENT)
    sw=d.textlength(stat,font=fs)
    d.text((30+sw+44,214),source.upper(),font=_f(32),fill=MUTED)
    im.save(SHARED/f"lt{n:02d}.png")

def gen_title(name, big, sub, sub_color=MUTED):
    W,H=1600,440
    im=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(im)
    fb=fit(d,big,W-40,150)
    bw=d.textlength(big,font=fb); d.text(((W-bw)/2,70),big,font=fb,fill=ACCENT)
    fs=_f(52); sw=d.textlength(sub,font=fs); d.text(((W-sw)/2,290),sub,font=fs,fill=sub_color)
    im.save(SHARED/f"{name}.png")

for n,h,s,src in DATA: gen_lowerthird(n,h,s,src)
gen_title("title_intro","AI NEWS","THE RUNDOWN  /  JULY 18, 2026",INK)
gen_title("title_outro","THAT'S THE RUNDOWN","see you next week",INK)
print("generated 14 lower-thirds + 2 title cards")

# ---- scenes -----------------------------------------------------------------
FINISH=dict(stutter_fps=12,lens=True,
            transition_in={"seconds":0.45,"blur_px":12,"push":0.05},
            transition_out={"seconds":0.45,"blur_px":12,"push":0.05})
def frac(f): return {"at_frac":f}
def write(seg,spec): (RUN/"scenes"/f"{seg}.collage.json").write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding="utf-8")
script={s["segment_id"]:s for s in json.loads((RUN/"script.json").read_text())["segments"]}

STORY=[("s01_gemini",1),("s02_codex",2),("s03_bonsai",3),("s04_chips",4),("s05_hyundai",5),
       ("s06_tiktok",6),("s07_race",7),("s08_voice",8),("s09_muse",9),("s10_meta",10),
       ("s11_ant",11),("s12_antidoom",12),("s13_hemispheric",13),("s14_nobel",14)]

def story_scene(seg,n):
    dur=script[seg]["estimated_duration_seconds"]; shot=f"shot{n:02d}"; lt=f"lt{n:02d}"
    pan=0.02 if n%2==0 else -0.02
    write(seg,{"spec_version":1,"segment_id":seg,"duration_seconds":float(dur),"fps":30,
      "style_pack":"anthropic_docu","background":"$palette.paper",
      "camera":[{"time":frac(0.0),"x":0.5,"y":0.5,"scale":1.0},{"time":frac(1.0),"x":0.5,"y":0.5,"scale":1.0}],
      "assets":[{"id":shot,"role":"subject","src":f"assets/shared/{shot}.png"},
                {"id":lt,"role":"subject","src":f"assets/shared/{lt}.png"}],
      "elements":[
        {"id":"dust","type":"particles","style":"dust","count":30,"color":"$palette.muted","depth":0.7},
        {"id":"shot","type":"layer","asset_id":shot,"x":0.5,"y":0.37,"width":0.68,"depth":0.0,"z":2,
         "enter":frac(0.03),"move":[{"time":frac(0.0),"x":0.5,"scale":1.0},{"time":frac(1.0),"x":0.5+pan,"scale":1.06}]},
        {"id":"num","type":"label","text":f"{n:02d} / 14","style":"mono","x":0.06,"y":0.08,"color":"$palette.accent","enter":frac(0.02)},
        {"id":"lt","type":"layer","asset_id":lt,"x":0.5,"y":0.86,"width":0.94,"depth":0.0,"z":6,"enter":frac(0.12)},
      ],**FINISH})

def intro():
    thumbs=[{"id":f"shot{n:02d}","role":"subject","src":f"assets/shared/shot{n:02d}.png"} for n in (2,8,14)]
    thumbs.append({"id":"title","role":"subject","src":"assets/shared/title_intro.png"})
    els=[{"id":"dust","type":"particles","style":"dust","count":46,"color":"$palette.muted","depth":0.7}]
    for i,(n,ang,x,y) in enumerate([(2,-4,0.2,0.36),(8,3,0.82,0.3),(14,-3,0.74,0.74)]):
        els.append({"id":f"th{i}","type":"layer","asset_id":f"shot{n:02d}","x":x,"y":y,"width":0.3,"depth":0.5,
                    "rotate":ang,"opacity":0.45,"z":1,"enter":frac(0.05+i*0.07),
                    "oscillate":{"axis":"y","amplitude":0.008,"period":3.0+i*0.4}})
    els+=[{"id":"title","type":"layer","asset_id":"title","x":0.5,"y":0.47,"width":0.62,"depth":0.0,"z":6,"enter":frac(0.12),
           "oscillate":{"axis":"scale","amplitude":0.008,"period":4.0}},
          {"id":"cnt","type":"label","text":"14 stories - fact-checked","x":0.35,"y":0.72,"enter":frac(0.6)}]
    write("s00_intro",{"spec_version":1,"segment_id":"s00_intro","duration_seconds":26.0,"fps":30,
      "style_pack":"anthropic_docu","background":"$palette.paper",
      "camera":[{"time":frac(0.0),"x":0.5,"y":0.5,"scale":1.04},{"time":frac(1.0),"x":0.5,"y":0.5,"scale":1.0}],
      "assets":thumbs,"elements":els,**FINISH})

def outro():
    thumbs=[{"id":f"shot{n:02d}","role":"subject","src":f"assets/shared/shot{n:02d}.png"} for n in (11,4,9)]
    thumbs.append({"id":"title","role":"subject","src":"assets/shared/title_outro.png"})
    els=[{"id":"glow","type":"particles","style":"biolume","count":38,"color":"$palette.sky","depth":0.7}]
    for i,(n,ang,x,y) in enumerate([(11,-4,0.22,0.34),(4,4,0.8,0.32),(9,-3,0.74,0.72)]):
        els.append({"id":f"th{i}","type":"layer","asset_id":f"shot{n:02d}","x":x,"y":y,"width":0.28,"depth":0.5,
                    "rotate":ang,"opacity":0.4,"z":1,"enter":frac(0.04+i*0.06),
                    "oscillate":{"axis":"rotate","amplitude":2.0,"period":3.4+i*0.3}})
    els+=[{"id":"title","type":"layer","asset_id":"title","x":0.5,"y":0.5,"width":0.66,"depth":0.0,"z":6,"enter":frac(0.1)}]
    write("s15_outro",{"spec_version":1,"segment_id":"s15_outro","duration_seconds":20.0,"fps":30,
      "style_pack":"anthropic_docu","background":"$palette.paper",
      "camera":[{"time":frac(0.0),"x":0.5,"y":0.5,"scale":1.0},{"time":frac(1.0),"x":0.5,"y":0.5,"scale":1.06}],
      "assets":thumbs,"elements":els,**FINISH})

intro()
for seg,n in STORY: story_scene(seg,n)
outro()
print("rewrote 16 scenes")
