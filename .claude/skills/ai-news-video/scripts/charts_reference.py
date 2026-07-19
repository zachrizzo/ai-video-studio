"""Generate clean, designed benchmark/stat charts (house palette) from the
verified numbers. 1600x900, paper bg, ink text, accent bars, generous margins,
Helvetica. Content is kept within the TOP ~78% of the canvas (y <= 700): when a
chart is placed as a full-bleed collage beat, the persistent lower-third overlays
the bottom band, so that band is left intentionally blank."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SHARED = Path("/Users/zachrizzo/.video-studio/runs/run_1eb772e9/assets/shared")
PAPER=(240,238,230); INK=(31,30,27); ACCENT=(217,119,87); MUTED=(138,135,120)
SKY=(150,170,178); FAINT=(214,210,198); INKSOFT=(90,88,80)
W,H=1600,900
SAFE=700  # nothing critical below this y

def _f(size, bold=True):
    idx = 1 if bold else 0
    for p,i in [("/System/Library/Fonts/Helvetica.ttc",idx),
                ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",0),
                ("/System/Library/Fonts/Helvetica.ttc",0)]:
        try: return ImageFont.truetype(p,size,index=i)
        except Exception: continue
    return ImageFont.load_default()

def canvas():
    im=Image.new("RGB",(W,H),PAPER); return im, ImageDraw.Draw(im)

def tw(d,t,f): return d.textlength(t,font=f)

def header(d, title, subtitle=None, tag=None):
    d.text((80,52), title, font=_f(58,True), fill=INK)
    d.rectangle([82,134,82+140,142], fill=ACCENT)
    if subtitle:
        d.text((84,156), subtitle, font=_f(28,False), fill=INKSOFT)
    if tag:
        f=_f(26,True); d.text((W-80-tw(d,tag,f),64), tag, font=f, fill=MUTED)

def save(im,name): im.save(SHARED/name); print("wrote",name)

# ---------------------------------------------------------------- bonsai perf
def chart_bonsai():
    im,d=canvas()
    header(d,"27B ON CONSUMER HARDWARE","Generation speed, tokens/sec (higher = better)",tag="PrismML")
    rows=[("RTX 5090  ·  1-bit",163,ACCENT),
          ("RTX 5090  ·  ternary",134,SKY),
          ("Apple M5 Max  ·  1-bit",87,ACCENT),
          ("Apple M5 Max  ·  ternary",58,SKY)]
    x0,w=90,1160; vmax=180; y=250; bh=80; gap=34
    for lbl,val,col in rows:
        d.text((x0,y-40), lbl, font=_f(32,True), fill=INK)
        bw=int(w*val/vmax)
        d.rounded_rectangle([x0,y,x0+bw,y+bh],radius=12,fill=col)
        d.text((x0+bw+22,y+bh//2-27), f"{val}", font=_f(52,True), fill=INK)
        d.text((x0+bw+22+tw(d,str(val),_f(52,True))+10,y+bh//2-4), "tok/s", font=_f(24,False), fill=MUTED)
        y+=bh+gap
    save(im,"chart_bonsai.png")

# ---------------------------------------------------------------- bonsai size
def chart_bonsai_size():
    im,d=canvas()
    header(d,"SHRINKING A 27B MODEL","On-disk size — same model, three precisions (GB)",tag="PrismML")
    rows=[("FP16  (full precision)",54.0,MUTED,"~54 GB"),
          ("Ternary  {-1, 0, +1}",5.9,ACCENT,"5.9 GB"),
          ("1-bit",3.9,ACCENT,"3.9 GB")]
    x0,w=90,1040; vmax=58; y=252; bh=100; gap=46
    for lbl,val,col,tag in rows:
        d.text((x0,y-42), lbl, font=_f(32,True), fill=INK)
        bw=max(14,int(w*val/vmax))
        d.rounded_rectangle([x0,y,x0+bw,y+bh],radius=12,fill=col)
        d.text((x0+bw+24,y+bh//2-32), tag, font=_f(54,True), fill=INK)
        y+=bh+gap
    d.text((90,676),"Frontier-class weights that once needed a server now fit on a laptop.",
           font=_f(28,False), fill=INKSOFT)
    save(im,"chart_bonsai_size.png")

# ---------------------------------------------------------------- antidoom
def chart_antidoom():
    im,d=canvas()
    header(d,"FIXING AI 'DOOM LOOPS'","Repetition-until-context-exhausted rate, before vs after (greedy)",tag="Liquid AI")
    groups=[("Qwen3.5-4B","third-party",22.9,1.0),
            ("LFM2.5-2.6B","Liquid checkpoint",10.2,1.4)]
    base_y=590; top_y=250; vmax=25.0
    gx=[430,1050]; bw=150; pairgap=190
    def barh(v): return int((base_y-top_y)*v/vmax)
    d.line([120,base_y,1500,base_y],fill=INK,width=4)
    for cx,(lbl,sub,before,after) in zip(gx,groups):
        for i,(v,col,cap) in enumerate([(before,MUTED,"before"),(after,ACCENT,"after")]):
            bx=cx-pairgap//2 + i*pairgap - bw//2
            h=barh(v)
            d.rounded_rectangle([bx,base_y-h,bx+bw,base_y],radius=12,fill=col)
            d.text((bx+bw//2-tw(d,f"{v:g}%",_f(44,True))//2, base_y-h-54), f"{v:g}%", font=_f(44,True), fill=INK)
            d.text((bx+bw//2-tw(d,cap,_f(24,False))//2, base_y+12), cap, font=_f(24,False), fill=MUTED)
        d.text((cx-tw(d,lbl,_f(32,True))//2, base_y+48), lbl, font=_f(32,True), fill=INK)
        d.text((cx-tw(d,sub,_f(24,False))//2, base_y+86), sub, font=_f(24,False), fill=MUTED)
    save(im,"chart_antidoom.png")

# ---------------------------------------------------------------- meta
def chart_meta():
    im,d=canvas()
    header(d,"META'S 2026 AI PARADOX","Record spend, deep cuts — and the payoff isn't here yet",tag="TechCrunch / Reuters")
    def block(x0,y0,w,h,big,label,col):
        d.rounded_rectangle([x0,y0,x0+w,y0+h],radius=24,outline=col,width=5)
        d.rectangle([x0,y0,x0+w,y0+14],fill=col)
        d.text((x0+46,y0+58), big, font=_f(108,True), fill=col)
        d.text((x0+46,y0+206), label, font=_f(32,True), fill=INK)
    block(90,246,660,372,"$125–145B","projected 2026 AI capital spending",ACCENT)
    block(830,246,660,372,"8,000","jobs cut in May  ·  ~10% of staff",INK)
    d.text((96,662),"July 2 town hall — Zuckerberg: AI agents “haven't accelerated the way we expected.”",
           font=_f(28,False), fill=INKSOFT)
    save(im,"chart_meta.png")

# ---------------------------------------------------------------- gemini timeline
def chart_gemini():
    im,d=canvas()
    header(d,"GEMINI 3.5 PRO: STILL NOT SHIPPED","Google's flagship keeps slipping while a rival went GA",tag="reporting / 9to5Google")
    y=430; x0,x1=140,1460
    d.line([x0,y,x1,y],fill=INK,width=5)
    miss=[("June 2026","original target","missed"),
          ("early July","rebuilt, slipped","missed"),
          ("July 17","new date, slipped","missed"),
          ("today","still no specs","unshipped")]
    xs=[x0+ (x1-x0)*i/3 for i in range(4)]
    for x,(dt,desc,st) in zip(xs,miss):
        d.ellipse([x-16,y-16,x+16,y+16],fill=PAPER,outline=MUTED,width=6)
        d.line([x-11,y-11,x+11,y+11],fill=MUTED,width=5); d.line([x-11,y+11,x+11,y-11],fill=MUTED,width=5)
        d.text((x-tw(d,dt,_f(32,True))//2,y-118), dt, font=_f(32,True), fill=INK)
        d.text((x-tw(d,desc,_f(24,False))//2,y-78), desc, font=_f(24,False), fill=MUTED)
        d.text((x-tw(d,st.upper(),_f(23,True))//2,y+38), st.upper(), font=_f(23,True), fill=MUTED)
    d.rounded_rectangle([230,586,1370,688],radius=18,fill=(232,229,219))
    d.ellipse([268,620,314,666],fill=ACCENT)
    d.text((334,618),"July 9 — OpenAI's GPT-5.6 reached general availability.  Shipped.",font=_f(32,True),fill=INK)
    save(im,"chart_gemini.png")

# ---------------------------------------------------------------- voice
def chart_voice():
    im,d=canvas()
    header(d,"USERS PREFER GPT-LIVE","Blind preference vs the old Advanced Voice Mode",tag="TechCrunch")
    x0,y0,w,h=100,330,1400,150
    a=int(w*0.757)
    d.rounded_rectangle([x0,y0,x0+w,y0+h],radius=20,fill=SKY)
    d.rounded_rectangle([x0,y0,x0+a,y0+h],radius=20,fill=ACCENT)
    d.text((x0+40,y0+h//2-52),"75.7%",font=_f(96,True),fill=(255,255,255))
    d.text((x0+40,y0+h+22),"preferred GPT-Live",font=_f(36,True),fill=INK)
    lbl="24.3%"; d.text((x0+w-40-tw(d,lbl,_f(54,True)),y0+h//2-33),lbl,font=_f(54,True),fill=(255,255,255))
    r="old voice mode"; d.text((x0+w-tw(d,r,_f(28,False)),y0+h+26),r,font=_f(28,False),fill=MUTED)
    d.text((100,634),"GPT-Live listens and speaks at once (full-duplex); the mini model is now the free default.",
           font=_f(28,False),fill=INKSOFT)
    save(im,"chart_voice.png")

# ---------------------------------------------------------------- kimi arena
def chart_kimi():
    im,d=canvas()
    header(d,"KIMI K3 TOPS THE WEBDEV ARENA","Arena.ai frontend-code leaderboard · Elo (third-party, blind preference)",
           tag="Arena.ai · reported")
    rows=[("Kimi K3   (open)",1679,ACCENT,True),
          ("Claude Fable 5",1631,MUTED,False),
          ("GPT-5.6 Sol",1618,MUTED,False)]
    base=1560; vmax=1700  # axis starts at 1560 (disclosed) to show separation
    x0,w=90,1100; y=250; bh=100; gap=46
    for lbl,val,col,star in rows:
        d.text((x0,y-42), lbl, font=_f(34,True), fill=INK)
        bw=max(20,int(w*(val-base)/(vmax-base)))
        d.rounded_rectangle([x0,y,x0+bw,y+bh],radius=12,fill=col)
        d.text((x0+bw+24,y+bh//2-30), f"{val}", font=_f(54,True), fill=INK)
        if star:
            d.text((x0+bw+24+tw(d,str(val),_f(54,True))+16,y+bh//2-20), "#1", font=_f(38,True), fill=ACCENT)
        y+=bh+gap
    d.text((90,y+2),"Elo axis starts at 1560 to show separation.  Open weights promised July 27 (reported Modified MIT).",
           font=_f(25,False), fill=INKSOFT)
    save(im,"chart_kimi.png")

# ---------------------------------------------------------------- ant stats
def chart_ant():
    im,d=canvas()
    header(d,"THE MONEY IS ON HUMANOIDS","Ant Group's bet on robotics startup Zeroth",tag="CNBC")
    def tile(x0,big,l1,l2):
        w,h=430,344; y0=272
        d.rounded_rectangle([x0,y0,x0+w,y0+h],radius=24,outline=ACCENT,width=5)
        d.rectangle([x0,y0,x0+w,y0+14],fill=ACCENT)
        f=_f(92,True); d.text((x0+w//2-tw(d,big,f)//2,y0+66),big,font=f,fill=INK)
        f1=_f(30,True); d.text((x0+w//2-tw(d,l1,f1)//2,y0+206),l1,font=f1,fill=INK)
        f2=_f(25,False); d.text((x0+w//2-tw(d,l2,f2)//2,y0+248),l2,font=f2,fill=MUTED)
    tile(95,"$73.6M","pre-Series A round","(500M yuan)")
    tile(585,"30,000+","unit orders","Zeroth says it holds")
    tile(1075,"12th","robotics deal","by Ant since 2025")
    save(im,"chart_ant.png")

for fn in [chart_bonsai,chart_bonsai_size,chart_antidoom,chart_meta,
           chart_gemini,chart_voice,chart_kimi,chart_ant]:
    fn()
print("done")
