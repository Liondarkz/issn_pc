#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update image posted with each release, in English and Portuguese: what is new and what
comes next, in the launcher's colours. The texts are a spec file per release
(packaging/release/<version>.json); the pictures are optional and never committed:

    python3 tools/release_image.py packaging/release/0.3.6.json --out DIR \
        [--shot-en launcher_en.png --shot-pt launcher_pt.png] [--backdrop own_screenshot.png]

--shot-*: a 1280x800 launcher screenshot (the area right of the side bar is used);
--backdrop: a picture blurred behind the header (your own capture). Needs Pillow and Inter.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
HEAD = str(ROOT / 'launcher' / 'issn_font.ttf')
INTER = '/usr/share/fonts/opentype/inter/Inter-Regular.otf'
INTERB = '/usr/share/fonts/opentype/inter/Inter-SemiBold.otf'
BG = (9, 9, 12); CARD = (24, 26, 33); LINE = (38, 41, 53); TEXT = (242, 242, 244); MUTED = (150, 154, 170)
RED = (226, 38, 59); RED_HI = (255, 67, 89); NEON = (36, 211, 242); PINK = (255, 62, 165)
BLURPLE = (88, 101, 242); GREEN = (80, 200, 120); GOLD = (245, 185, 66)
COLORS = {'red': RED_HI, 'neon': NEON, 'pink': PINK, 'green': GREEN, 'gold': GOLD}


def F(path, size):
    return ImageFont.truetype(path, size)


def wrap(d, text, font, width):
    words, lines, cur = text.split(), [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if d.textlength(t, font=font) <= width: cur = t
        else: lines.append(cur); cur = w
    lines.append(cur); return lines

def make(t, out, shot_path=None, backdrop=None):
    t=dict(t); t['cards']=[(COLORS[c],a,b) for c,a,b in t['cards']]
    W,H=1600,1440
    img=Image.new('RGB',(W,H),BG)
    if backdrop:
        bgshot=Image.open(backdrop).convert('RGB')
        bd=bgshot.resize((W,int(W*bgshot.height/bgshot.width))).filter(ImageFilter.GaussianBlur(34))
        bd=Image.blend(Image.new('RGB',bd.size,BG),bd,0.20)
        img.paste(bd,(0,0))
    fade=Image.new('L',(1,H)); 
    for y in range(H): fade.putpixel((0,y), min(255,int(255*max(0,(y-500))/500)))
    img.paste(Image.new('RGB',(W,H),BG),(0,0),fade.resize((W,H)))
    d=ImageDraw.Draw(img)
    d.rectangle((0,0,W,8),fill=RED)
    d.text((60,36),'INFAMOUS SECOND SON',font=F(HEAD,88),fill=TEXT)
    d.text((60,126),t['sub'],font=F(HEAD,48),fill=RED_HI)
    for i,c in enumerate((RED,PINK,NEON)): d.rectangle((60+i*170,190,60+i*170+160,194),fill=c)
    # hero: XeSS on the left, the launcher on the right
    y0=236
    if shot_path:
        shot=Image.open(shot_path).convert('RGB').crop((258,110,1270,462))
        sw=780; sh=int(sw*shot.height/shot.width); sx=W-60-sw
        d.text((sx,y0),t['shot'],font=F(HEAD,32),fill=MUTED)
        d.rounded_rectangle((sx-3,y0+44,sx+sw+3,y0+50+sh),10,fill=LINE)
        img.paste(shot.resize((sw,sh),Image.LANCZOS),(sx,y0+47))
        hw=sx-60-40
    else:
        sh=0; hw=W-120
    d.text((60,y0-6),t['hero'],font=F(HEAD,76),fill=NEON)
    yy=y0+90
    for line in wrap(d,t['hero_sub'],F(INTER,25),hw):
        d.text((60,yy),line,font=F(INTER,25),fill=TEXT); yy+=36
    yy+=14
    for p in t['hero_pts']:
        d.ellipse((60,yy+11,72,yy+23),fill=NEON)
        for k,line in enumerate(wrap(d,p,F(INTERB,24),hw-30)):
            d.text((86,yy+k*32),line,font=F(INTERB,24),fill=TEXT)
        yy+=32*len(wrap(d,p,F(INTERB,24),hw-30))+12
    y=max(yy,y0+50+sh)+44
    d.text((60,y),t['new'],font=F(HEAD,44),fill=TEXT); y+=62
    gap=24; cw=(W-120-gap)//2
    for i,(c,ti,sub) in enumerate(t['cards']):
        xx=60+(i%2)*(cw+gap); yy=y+(i//2)*108
        d.rounded_rectangle((xx,yy,xx+cw,yy+94),14,fill=CARD)
        d.rectangle((xx,yy+14,xx+5,yy+80),fill=c)
        d.text((xx+26,yy+14),ti,font=F(INTERB,28),fill=TEXT)
        d.text((xx+26,yy+54),sub,font=F(INTER,21),fill=MUTED)
    y+=3*108+16
    d.text((60,y),t['also'],font=F(HEAD,40),fill=TEXT)
    start=60+d.textlength(t['also'],font=F(HEAD,40))+36
    size,gap2=23,34
    while start+sum(20+d.textlength(p,font=F(INTER,size))+gap2 for p in t['also_pts'])-gap2>W-60:
        size-=1; gap2=max(22,gap2-2)
    xx=start
    for p in t['also_pts']:
        d.ellipse((xx,y+18,xx+10,y+28),fill=RED_HI); d.text((xx+20,y+8+(23-size)//2),p,font=F(INTER,size),fill=TEXT)
        xx+=20+d.textlength(p,font=F(INTER,size))+gap2
    y+=72
    # coming next
    d.rounded_rectangle((60,y,W-60,y+196),18,fill=(16,30,38),outline=NEON,width=2)
    d.text((92,y+20),t['next'],font=F(HEAD,44),fill=NEON)
    d.text((92+d.textlength(t['next'],font=F(HEAD,44))+24,y+38),t['next_tag'],font=F(INTER,21),fill=MUTED)
    for i,p in enumerate(t['next_pts']):
        xx=92+(i%2)*((W-184)//2); yy=y+90+(i//2)*48
        d.text((xx,yy),'→',font=F(INTERB,26),fill=NEON)
        d.text((xx+38,yy),p,font=F(INTERB,26),fill=TEXT)
    by=H-130
    d.rounded_rectangle((60,by,W-60,H-40),18,fill=BLURPLE)
    d.text((96,by+14),t['disc'],font=F(HEAD,44),fill=(255,255,255))
    d.text((96,by+60),t['disc_sub'],font=F(INTER,22),fill=(225,228,255))
    d.text((W-96,by+45),'discord.gg/y82HQ5uTQg',font=F(INTERB,40),fill=(255,255,255),anchor='rm')
    img.save(out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('spec')
    parser.add_argument('--out', default='.')
    parser.add_argument('--shot-en'); parser.add_argument('--shot-pt')
    parser.add_argument('--backdrop')
    args = parser.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding='utf-8'))
    for lang in ('en', 'pt'):
        out = Path(args.out) / f"issn-{spec['version']}-update-{lang}.png"
        print(make(spec[lang], str(out), getattr(args, f'shot_{lang}'), args.backdrop))


if __name__ == '__main__':
    main()
