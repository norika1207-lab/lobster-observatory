"""Hero banner v5 — real card photos, H=240 to match lobby hero div"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import math, random, requests, os

W, H = 1300, 240
random.seed(99)

APP_DIR = '/opt/battle_arena'

try:
    cards = requests.get('http://localhost:8004/api/cards', timeout=5).json()['cards']
    cards = [c for c in cards if c.get('atk', 0) >= 25 and c.get('image_url')]
except:
    cards = []

ELEM_COL = {'fire':(255,80,30),'water':(30,180,255),'wind':(80,210,255),'earth':(100,255,80)}

try:
    FONT_ATK  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 34)
    FONT_NAME = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
    FONT_LG   = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 46)
    FONT_MED  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 34)
    FONT_SUB  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 13)
except:
    FONT_ATK = FONT_NAME = FONT_LG = FONT_MED = FONT_SUB = ImageFont.load_default()


def load_photo(url):
    path = APP_DIR + url   # url is like '/static/cards/f1/ver.png'
    if not os.path.exists(path):
        return None
    try:
        return Image.open(path).convert('RGB')
    except:
        return None


def make_card(card, w, h):
    col = ELEM_COL.get(card.get('element'), (180,180,255))
    r, g, b = col
    INFO_H = 44

    # Start with opaque dark bg — rounded mask applied at very end
    frame = Image.new('RGBA', (w, h), (6, 8, 20, 255))

    # ── real photo fills top portion ──
    photo = load_photo(card.get('image_url',''))
    if photo:
        pw, ph_src = photo.size
        scale = w / pw
        new_h = int(ph_src * scale)
        photo = photo.resize((w, new_h), Image.LANCZOS)
        photo_h = h - INFO_H
        if new_h < photo_h:
            photo = photo.resize((w, photo_h), Image.LANCZOS)
        photo = photo.crop((0, 0, w, photo_h))
        photo = ImageEnhance.Brightness(photo).enhance(1.2)
        photo = ImageEnhance.Contrast(photo).enhance(1.15)
        photo = ImageEnhance.Color(photo).enhance(1.55)
        frame.paste(photo.convert('RGBA'), (0, 0))  # paste solid — no mask mismatch

    d = ImageDraw.Draw(frame)

    # ── info strip ──
    strip_y = h - INFO_H
    d.rectangle([0, strip_y, w, h], fill=(5, 7, 18, 245))
    d.line([(0, strip_y), (w, strip_y)], fill=(*col, 230), width=2)

    # ── element glow border ──
    for i in range(5):
        a = 255 - i * 44
        if a <= 0: break
        d.rounded_rectangle([i,i,w-1-i,h-1-i], radius=10, outline=(r,g,b,a), width=1)

    # ── ATK ──
    atk = str(card.get('atk','?'))
    mid_strip = strip_y + INFO_H // 2 - 4
    d.text((w//2+1, mid_strip+1), atk, font=FONT_ATK, fill=(0,0,0,200), anchor='mm')
    d.text((w//2,   mid_strip),   atk, font=FONT_ATK, fill=(r,g,b,255),  anchor='mm')

    # ── name ──
    name = card.get('name_short','')[:10].upper()
    d.text((w//2+1, h-7+1), name, font=FONT_NAME, fill=(0,0,0,180), anchor='mm')
    d.text((w//2,   h-7),   name, font=FONT_NAME, fill=(220,235,255,240), anchor='mm')

    # ── apply rounded corner mask ──
    mask = Image.new('L', (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0,0,w-1,h-1], radius=10, fill=255)
    frame.putalpha(mask)

    return frame


def paste_rotated(base, card_img, cx, cy, angle_deg, alpha=1.0):
    # shadow
    sh = Image.new('RGBA',(W,H),(0,0,0,0))
    rot_sh = card_img.rotate(angle_deg, expand=True, resample=Image.BICUBIC)
    _, _, _, a_ch = rot_sh.split()
    sh_card = Image.new('RGBA', rot_sh.size, (0,0,0,0))
    sh_card.putalpha(a_ch.point(lambda x: int(x*0.5)))
    ox = cx - rot_sh.width//2 + 7
    oy = cy - rot_sh.height//2 + 9
    sh.paste(sh_card, (ox, oy), sh_card)
    sh_blur = sh.filter(ImageFilter.GaussianBlur(9))
    base.paste(sh_blur, (0,0), sh_blur)

    rotated = card_img.rotate(angle_deg, expand=True, resample=Image.BICUBIC)
    if alpha < 1.0:
        rv,gv,bv,av = rotated.split()
        av = av.point(lambda x: int(x*alpha))
        rotated = Image.merge('RGBA',(rv,gv,bv,av))
    px = cx - rotated.width//2
    py = cy - rotated.height//2
    base.paste(rotated, (px,py), rotated)


def lightning(d, x1,y1,x2,y2,col,w=2,segs=9):
    pts = [(x1,y1)]
    for i in range(1,segs):
        t=i/segs
        pts.append((int(x1+(x2-x1)*t+random.randint(-14,14)),
                    int(y1+(y2-y1)*t+random.randint(-9,9))))
    pts.append((x2,y2))
    for i in range(len(pts)-1):
        d.line([pts[i],pts[i+1]], fill=(*col,45),  width=w+4)
        d.line([pts[i],pts[i+1]], fill=(*col,215), width=w)


# ────────────── BUILD ──────────────
canvas = Image.new('RGBA', (W,H), (0,0,0,255))
bg = ImageDraw.Draw(canvas)

for y in range(H):
    t = y/H
    bg.line([(0,y),(W,y)], fill=(int(4+t*4), int(6+t*5), int(16+t*8)))

for x in range(0,W,55): bg.line([(x,0),(x,H)], fill=(0,200,255,7))
for y in range(0,H,55): bg.line([(0,y),(W,y)], fill=(0,200,255,7))

CX, CY = W//2, H//2 + 12   # 132

# radial glow
glow = Image.new('RGBA',(W,H),(0,0,0,0))
for gr in range(240,0,-5):
    ga = max(0, int(28*(1-gr/240)))
    ImageDraw.Draw(glow).ellipse([CX-gr,CY-gr,CX+gr,CY+gr], fill=(0,150,255,ga))
canvas = Image.alpha_composite(canvas, glow)

# ── CARDS ──
CW, CH = 148, 210

left_pool  = cards[:3]
right_pool = cards[3:6]

# left team fan toward centre
L = [
    (-460, 18, +34, 0.68, 0.66),
    (-310,  7, +21, 0.83, 0.85),
    (-175,  0, + 7, 1.00, 1.00),
]
for i,(dx,dy,ang,sc,al) in enumerate(L[:len(left_pool)]):
    c = left_pool[i]
    cw,ch = int(CW*sc), int(CH*sc)
    ci = make_card(c, cw, ch)
    col = ELEM_COL.get(c.get('element'),(180,180,255))
    halo = Image.new('RGBA',(W,H),(0,0,0,0))
    for hr in range(55,0,-5):
        ha = int(20*(1-hr/55)*al)
        ImageDraw.Draw(halo).ellipse([CX+dx-hr,CY+dy-hr,CX+dx+hr,CY+dy+hr],fill=(*col,ha))
    canvas = Image.alpha_composite(canvas, halo)
    paste_rotated(canvas, ci, CX+dx, CY+dy, ang, al)

# right team fan toward centre
R = [
    (+460, 18, -34, 0.68, 0.66),
    (+310,  7, -21, 0.83, 0.85),
    (+175,  0, - 7, 1.00, 1.00),
]
for i,(dx,dy,ang,sc,al) in enumerate(R[:len(right_pool)]):
    c = right_pool[i]
    cw,ch = int(CW*sc), int(CH*sc)
    ci = make_card(c, cw, ch)
    col = ELEM_COL.get(c.get('element'),(180,180,255))
    halo = Image.new('RGBA',(W,H),(0,0,0,0))
    for hr in range(55,0,-5):
        ha = int(20*(1-hr/55)*al)
        ImageDraw.Draw(halo).ellipse([CX+dx-hr,CY+dy-hr,CX+dx+hr,CY+dy+hr],fill=(*col,ha))
    canvas = Image.alpha_composite(canvas, halo)
    paste_rotated(canvas, ci, CX+dx, CY+dy, ang, al)

# ── CLASH BURST ──
burst = Image.new('RGBA',(W,H),(0,0,0,0))
bd = ImageDraw.Draw(burst)
for ring_r,ring_a,ring_col in [
    (38,  240, (255,255,255)),
    (70,  150, (0,230,255)),
    (108,  85, (255,210,0)),
    (155,  48, (255,90,20)),
    (200,  22, (200,0,255)),
]:
    bd.ellipse([CX-ring_r,CY-ring_r,CX+ring_r,CY+ring_r], fill=(*ring_col,ring_a))
canvas = Image.alpha_composite(canvas, burst)
canvas = Image.alpha_composite(canvas, burst.filter(ImageFilter.GaussianBlur(14)))
core = Image.new('RGBA',(W,H),(0,0,0,0))
ImageDraw.Draw(core).ellipse([CX-30,CY-30,CX+30,CY+30], fill=(255,255,255,245))
ImageDraw.Draw(core).ellipse([CX-14,CY-14,CX+14,CY+14], fill=(255,255,255,255))
canvas = Image.alpha_composite(canvas, core)

draw_d = ImageDraw.Draw(canvas)
for tx,ty in [(CX-170,CY-55),(CX+170,CY-55),(CX-130,CY+65),(CX+130,CY+65),(CX,CY-110)]:
    lc = random.choice([(0,225,255),(255,205,0),(255,100,30),(200,100,255)])
    lightning(draw_d, CX, CY, tx, ty, lc, w=2)

for _ in range(90):
    ang = random.uniform(0, math.pi*2)
    dist = random.uniform(10, 180)
    px2 = CX + int(math.cos(ang)*dist)
    py2 = CY + int(math.sin(ang)*dist)
    pr  = random.randint(1,3)
    pc  = random.choice([(0,225,255),(255,205,0),(255,90,30),(255,255,255),(200,100,255)])
    draw_d.ellipse([px2-pr,py2-pr,px2+pr,py2+pr], fill=(*pc,200))

for _ in range(20):
    side = random.choice([-1,1])
    sx = random.randint(0,W//2-160) if side==-1 else random.randint(W//2+160,W)
    sy = random.randint(8,H-8)
    ex = CX + random.randint(-40,40)
    ey = CY + random.randint(-28,28)
    c_sl = random.choice([(0,225,255,30),(255,195,0,24),(255,255,255,16)])
    draw_d.line([(sx,sy),(ex,ey)], fill=c_sl, width=1)

# ── TEXT (centred, strong shadow so it reads over any bg) ──
for dx2,dy2 in [(-3,3),(3,3),(-3,-3),(3,-3),(0,5)]:
    draw_d.text((CX+dx2, 30+dy2), 'SEASON CHALLENGE!', font=FONT_LG, fill=(0,0,0,230), anchor='mm')
draw_d.text((CX, 30), 'SEASON CHALLENGE!', font=FONT_LG, fill=(255,255,255,255), anchor='mm')

for dx2,dy2 in [(-2,2),(2,2),(0,3)]:
    draw_d.text((CX+dx2, 78+dy2), '10,000,000 PIT', font=FONT_MED, fill=(100,60,0,220), anchor='mm')
draw_d.text((CX, 78), '10,000,000 PIT', font=FONT_MED, fill=(255,215,0,255), anchor='mm')

draw_d.text((CX, 112), 'BIGGEST POT EVER  ·  LIMITED TIME  ·  WINNER TAKES ALL',
            font=FONT_SUB, fill=(140,162,190,210), anchor='mm')

# ── VIGNETTE ──
vig = Image.new('RGBA',(W,H),(0,0,0,0))
for i in range(0,70,2):
    ImageDraw.Draw(vig).rectangle([i,i,W-1-i,H-1-i], outline=(0,0,0,int(i*2.6)))
canvas = Image.alpha_composite(canvas, vig)

canvas = canvas.filter(ImageFilter.SHARPEN)
canvas.convert('RGB').save('/opt/battle_arena/static/hero_banner.png', 'PNG', optimize=True)
print(f'Saved hero_banner.png  {W}x{H}')
