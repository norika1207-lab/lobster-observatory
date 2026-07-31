"""Anime cat mascot hero banner — JP anime thick-paint style"""
import cairosvg, math

W, H = 1300, 320

# ── helpers ──────────────────────────────────────────────────
def star(cx, cy, r, n=5, ir=None, fill='white', opacity=1.0):
    if ir is None: ir = r * 0.4
    pts = []
    for i in range(n * 2):
        a = math.pi * i / n - math.pi / 2
        rad = r if i % 2 == 0 else ir
        pts.append(f"{cx + math.cos(a)*rad:.1f},{cy + math.sin(a)*rad:.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="{fill}" opacity="{opacity}"/>'

def sparkle(cx, cy, r, fill='white', opacity=0.8):
    lines = []
    for angle in [0, 45, 90, 135]:
        a = math.radians(angle)
        x1 = cx + math.cos(a) * r
        y1 = cy + math.sin(a) * r
        x2 = cx - math.cos(a) * r
        y2 = cy - math.sin(a) * r
        lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{fill}" stroke-width="1.5" opacity="{opacity}"/>')
    return '\n'.join(lines)

# ── SVG assembly ─────────────────────────────────────────────
parts = []

# ── 1. DEFS ──────────────────────────────────────────────────
parts.append(f'''<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">
<defs>
  <!-- Background -->
  <linearGradient id="bgGrad" x1="0" y1="0" x2="0.3" y2="1">
    <stop offset="0%" stop-color="#12082e"/>
    <stop offset="55%" stop-color="#0e0626"/>
    <stop offset="100%" stop-color="#08041a"/>
  </linearGradient>

  <!-- Spotlight behind main cat -->
  <radialGradient id="spotlight" cx="50%" cy="45%" r="55%">
    <stop offset="0%"  stop-color="#fff8e0" stop-opacity="0.18"/>
    <stop offset="60%" stop-color="#ffe090" stop-opacity="0.06"/>
    <stop offset="100%" stop-color="#ffcc44" stop-opacity="0"/>
  </radialGradient>

  <!-- Main cat fur: warm cream, radial shading -->
  <radialGradient id="furMain" cx="38%" cy="30%" r="72%">
    <stop offset="0%"   stop-color="#FFF8EC"/>
    <stop offset="45%"  stop-color="#F8E8C8"/>
    <stop offset="80%"  stop-color="#EDD6A8"/>
    <stop offset="100%" stop-color="#D9BE8A"/>
  </radialGradient>
  <!-- Fur shadow patch -->
  <radialGradient id="furShadow" cx="60%" cy="70%" r="55%">
    <stop offset="0%"   stop-color="#B89060" stop-opacity="0.55"/>
    <stop offset="100%" stop-color="#B89060" stop-opacity="0"/>
  </radialGradient>

  <!-- Defeated cat fur (simpler, grayer) -->
  <radialGradient id="furDefeated" cx="40%" cy="30%" r="70%">
    <stop offset="0%"   stop-color="#F5F0E8"/>
    <stop offset="100%" stop-color="#CCBFA0"/>
  </radialGradient>

  <!-- Eye iris: golden amber -->
  <radialGradient id="eyeIris" cx="35%" cy="30%" r="70%">
    <stop offset="0%"   stop-color="#FFD040"/>
    <stop offset="50%"  stop-color="#E89010"/>
    <stop offset="100%" stop-color="#B06000"/>
  </radialGradient>

  <!-- Eye iris green (alternate for defeated cats) -->
  <radialGradient id="eyeIrisG" cx="35%" cy="30%" r="70%">
    <stop offset="0%"   stop-color="#80FF80"/>
    <stop offset="50%"  stop-color="#20C040"/>
    <stop offset="100%" stop-color="#008020"/>
  </radialGradient>

  <!-- Outfit: deep indigo-purple cloak -->
  <linearGradient id="cloakGrad" x1="0" y1="0" x2="0.5" y2="1">
    <stop offset="0%"   stop-color="#3a2880"/>
    <stop offset="100%" stop-color="#1e1448"/>
  </linearGradient>

  <!-- Card face colors (5 element-coded cards) -->
  <!-- fire, wind, water, earth, special -->

  <!-- Blush filter: soft pink -->
  <filter id="blurBlush" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="4"/>
  </filter>
  <filter id="softGlow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="3" result="blur"/>
    <feComposite in="SourceGraphic" in2="blur" operator="over"/>
  </filter>
  <filter id="bigGlow">
    <feGaussianBlur stdDeviation="8" result="blur"/>
    <feComposite in="SourceGraphic" in2="blur" operator="over"/>
  </filter>

  <!-- Ground gradient -->
  <linearGradient id="groundGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%"   stop-color="#1a0e40" stop-opacity="0"/>
    <stop offset="100%" stop-color="#100830" stop-opacity="0.9"/>
  </linearGradient>

  <!-- Petal pink -->
  <radialGradient id="petGrad" cx="50%" cy="40%" r="60%">
    <stop offset="0%"   stop-color="#FFB8D0"/>
    <stop offset="100%" stop-color="#FF80A0"/>
  </radialGradient>
</defs>
''')

# ── 2. BACKGROUND ──────────────────────────────────────────
parts.append(f'<rect width="{W}" height="{H}" fill="url(#bgGrad)"/>')

# Star field
import random
random.seed(77)
star_svg = []
for _ in range(90):
    sx = random.randint(0, W)
    sy = random.randint(0, int(H*0.85))
    sr = random.uniform(0.5, 2.5)
    sa = random.uniform(0.2, 0.9)
    star_svg.append(f'<circle cx="{sx}" cy="{sy}" r="{sr:.1f}" fill="white" opacity="{sa:.2f}"/>')
parts.append('\n'.join(star_svg))

# Card suits floating in bg (very faint)
suit_positions = [(80,60,'♠'),(200,40,'♥'),(350,80,'♦'),(500,35,'♣'),
                  (650,60,'♠'),(750,45,'♥'),(850,25,'♦')]
for sx,sy,suit in suit_positions:
    col = '#ff80a0' if suit in ('♥','♦') else '#c0d0ff'
    parts.append(f'<text x="{sx}" y="{sy}" font-size="22" fill="{col}" opacity="0.08" '
                 f'font-family="serif">{suit}</text>')

# Spotlight behind main cat
parts.append('<ellipse cx="1010" cy="155" rx="280" ry="180" fill="url(#spotlight)"/>')

# Ground (subtle)
parts.append(f'<rect x="0" y="265" width="{W}" height="55" fill="url(#groundGrad)"/>')
parts.append(f'<line x1="0" y1="288" x2="{W}" y2="288" stroke="#6040c0" stroke-width="1" opacity="0.3"/>')

# ── 3. HELPER: draw an anime eye ───────────────────────────
# Returns SVG string for one anime eye at (cx,cy) size (rx,ry)
def anime_eye(cx, cy, rx, ry, iris='url(#eyeIris)', xeyes=False, swirl=False, closed=False):
    if closed:
        # Simple arc/squiggle for closed/sleeping eyes
        return (f'<path d="M {cx-rx} {cy} Q {cx} {cy-ry*0.5} {cx+rx} {cy}" '
                f'fill="none" stroke="#2a1505" stroke-width="2.5" stroke-linecap="round"/>')
    if xeyes:
        return (f'<text x="{cx}" y="{cy+ry*0.4:.0f}" text-anchor="middle" '
                f'font-size="{int(ry*1.6)}" fill="#2a1505" font-family="sans-serif" '
                f'font-weight="bold">✕</text>')
    if swirl:
        # Swirl/spiral eyes: ~
        return (f'<text x="{cx}" y="{cy+ry*0.4:.0f}" text-anchor="middle" '
                f'font-size="{int(ry*1.5)}" fill="#2a1505" font-family="sans-serif">~</text>')

    s = []
    # Sclera (white)
    s.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" '
             f'fill="white" stroke="#2a1505" stroke-width="2"/>')
    # Iris
    s.append(f'<ellipse cx="{cx}" cy="{cy+ry*0.1:.1f}" rx="{rx*0.72:.1f}" ry="{ry*0.78:.1f}" '
             f'fill="{iris}" />')
    # Pupil (vertical slit like a cat)
    s.append(f'<ellipse cx="{cx}" cy="{cy+ry*0.12:.1f}" rx="{rx*0.28:.1f}" ry="{ry*0.6:.1f}" '
             f'fill="#1a0a00"/>')
    # Upper eyelid (darker bar — gives anime look)
    s.append(f'<path d="M {cx-rx} {cy-ry*0.1:.1f} '
             f'Q {cx} {cy-ry*1.05:.1f} {cx+rx} {cy-ry*0.1:.1f}" '
             f'fill="none" stroke="#2a1505" stroke-width="3" stroke-linecap="round"/>')
    # Main highlight (upper-left quadrant)
    s.append(f'<ellipse cx="{cx-rx*0.28:.1f}" cy="{cy-ry*0.42:.1f}" '
             f'rx="{rx*0.28:.1f}" ry="{ry*0.26:.1f}" fill="white" opacity="0.95"/>')
    # Secondary highlight (small circle)
    s.append(f'<circle cx="{cx+rx*0.22:.1f}" cy="{cy-ry*0.18:.1f}" '
             f'r="{rx*0.12:.1f}" fill="white" opacity="0.7"/>')
    return '\n'.join(s)

# ── 4. HELPER: draw anime cat face ─────────────────────────
def cat_face(cx, cy, r, xeyes=False, swirl=False, closed=False,
             iris='url(#eyeIris)', fur='url(#furMain)', shadow='url(#furShadow)',
             outline='#2a1505', ear_col='#F8E0C0', ear_in='#FFB0C8',
             blush=True, brow=True, lw=2.5):

    s = []
    # Ear shadows (behind head)
    # Left ear
    s.append(f'<polygon points="{cx-r*0.42:.0f},{cy-r*0.25:.0f} {cx-r*0.82:.0f},{cy-r*1.28:.0f} {cx-r*0.08:.0f},{cy-r*0.68:.0f}" '
             f'fill="{ear_col}" stroke="{outline}" stroke-width="{lw}" stroke-linejoin="round"/>')
    # Left ear inner
    s.append(f'<polygon points="{cx-r*0.44:.0f},{cy-r*0.30:.0f} {cx-r*0.74:.0f},{cy-r*1.12:.0f} {cx-r*0.12:.0f},{cy-r*0.66:.0f}" '
             f'fill="{ear_in}"/>')
    # Right ear
    s.append(f'<polygon points="{cx+r*0.42:.0f},{cy-r*0.25:.0f} {cx+r*0.82:.0f},{cy-r*1.28:.0f} {cx+r*0.08:.0f},{cy-r*0.68:.0f}" '
             f'fill="{ear_col}" stroke="{outline}" stroke-width="{lw}" stroke-linejoin="round"/>')
    # Right ear inner
    s.append(f'<polygon points="{cx+r*0.44:.0f},{cy-r*0.30:.0f} {cx+r*0.74:.0f},{cy-r*1.12:.0f} {cx+r*0.12:.0f},{cy-r*0.66:.0f}" '
             f'fill="{ear_in}"/>')

    # Head circle
    s.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r*1.04:.1f}" '
             f'fill="{fur}" stroke="{outline}" stroke-width="{lw}"/>')
    # Shadow on head
    s.append(f'<ellipse cx="{cx+r*0.18:.1f}" cy="{cy+r*0.22:.1f}" rx="{r*0.7:.1f}" ry="{r*0.65:.1f}" '
             f'fill="{shadow}" opacity="0.6"/>')

    # Eyes
    ex_off = r * 0.36
    ey_off = r * 0.06
    er_x = r * 0.22
    er_y = r * 0.27
    s.append(anime_eye(cx - ex_off, cy + ey_off, er_x, er_y, iris, xeyes, swirl, closed))
    s.append(anime_eye(cx + ex_off, cy + ey_off, er_x, er_y, iris, xeyes, swirl, closed))

    if brow and not xeyes and not swirl and not closed:
        # Eyebrows (short curved arcs)
        bx = ex_off
        by = ey_off - er_y * 1.3
        s.append(f'<path d="M {cx-bx-er_x*0.5:.1f} {cy+by-r*0.03:.1f} '
                 f'Q {cx-bx:.1f} {cy+by-r*0.08:.1f} {cx-bx+er_x*0.5:.1f} {cy+by:.1f}" '
                 f'fill="none" stroke="#2a1505" stroke-width="2" stroke-linecap="round"/>')
        s.append(f'<path d="M {cx+bx-er_x*0.5:.1f} {cy+by:.1f} '
                 f'Q {cx+bx:.1f} {cy+by-r*0.08:.1f} {cx+bx+er_x*0.5:.1f} {cy+by-r*0.03:.1f}" '
                 f'fill="none" stroke="#2a1505" stroke-width="2" stroke-linecap="round"/>')

    # Nose (small pink inverted triangle)
    ny = cy + r * 0.26
    ns = r * 0.07
    s.append(f'<polygon points="{cx:.0f},{ny-ns:.0f} {cx-ns*1.2:.0f},{ny+ns*0.6:.0f} {cx+ns*1.2:.0f},{ny+ns*0.6:.0f}" '
             f'fill="#FF9AAA" stroke="{outline}" stroke-width="1"/>')

    # Mouth (cat W-shape)
    my = ny + ns * 1.6
    ms = r * 0.11
    s.append(f'<path d="M {cx-ms*1.4:.1f} {my:.1f} '
             f'Q {cx-ms*0.5:.1f} {my+ms*0.7:.1f} {cx:.1f} {my+ms*0.1:.1f} '
             f'Q {cx+ms*0.5:.1f} {my+ms*0.7:.1f} {cx+ms*1.4:.1f} {my:.1f}" '
             f'fill="none" stroke="{outline}" stroke-width="1.8" stroke-linecap="round"/>')

    # Whiskers
    wy = ny + ns * 0.5
    for i in range(3):
        ang_l = math.radians(175 - i * 12)
        ang_r = math.radians(5 + i * 12)
        wl = r * 0.55
        wx1 = cx - ns * 1.5 + math.cos(ang_l) * wl
        wy1 = wy + math.sin(ang_l) * wl
        wx2 = cx + ns * 1.5 + math.cos(ang_r) * wl
        wy2 = wy + math.sin(ang_r) * wl
        s.append(f'<line x1="{cx-ns*1.5:.1f}" y1="{wy:.1f}" x2="{wx1:.1f}" y2="{wy1:.1f}" '
                 f'stroke="{outline}" stroke-width="1.2" opacity="0.7"/>')
        s.append(f'<line x1="{cx+ns*1.5:.1f}" y1="{wy:.1f}" x2="{wx2:.1f}" y2="{wy2:.1f}" '
                 f'stroke="{outline}" stroke-width="1.2" opacity="0.7"/>')

    # Cheek blush (semi-transparent pink circles, blurred)
    if blush and not xeyes:
        bly = cy + r * 0.15
        s.append(f'<ellipse cx="{cx-r*0.56:.1f}" cy="{bly:.1f}" rx="{r*0.18:.1f}" ry="{r*0.1:.1f}" '
                 f'fill="#FF8095" opacity="0.40" filter="url(#blurBlush)"/>')
        s.append(f'<ellipse cx="{cx+r*0.56:.1f}" cy="{bly:.1f}" rx="{r*0.18:.1f}" ry="{r*0.1:.1f}" '
                 f'fill="#FF8095" opacity="0.40" filter="url(#blurBlush)"/>')
    return '\n'.join(s)

# ── 5. CARD helper ─────────────────────────────────────────
def card_svg(cx, cy, w, h, elem_col, label='', atk='', angle=0, rx=3):
    """Draw a game card at angle, centred at cx,cy."""
    cols = {
        'fire':  ('#1a0808', '#FF4820', '#FF8060'),
        'wind':  ('#081820', '#00CCFF', '#80EEFF'),
        'water': ('#081020', '#2060FF', '#80AAFF'),
        'earth': ('#081808', '#40FF40', '#A0FFA0'),
        'gold':  ('#201000', '#FFD040', '#FFE898'),
    }
    bg, border, text_col = cols.get(elem_col, cols['gold'])
    s = []
    s.append(f'<g transform="rotate({angle},{cx},{cy})">')
    # Card body
    x0, y0 = cx - w//2, cy - h//2
    s.append(f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
             f'fill="{bg}" stroke="{border}" stroke-width="2"/>')
    # Gradient strip at top
    s.append(f'<rect x="{x0+2}" y="{y0+2}" width="{w-4}" height="{h//4}" rx="2" '
             f'fill="{border}" opacity="0.5"/>')
    # ATK number
    if atk:
        s.append(f'<text x="{cx}" y="{cy+h*0.28:.0f}" text-anchor="middle" '
                 f'font-family="sans-serif" font-weight="bold" font-size="{h//3}" '
                 f'fill="{border}">{atk}</text>')
    # Label
    if label:
        s.append(f'<text x="{cx}" y="{y0+h-4}" text-anchor="middle" '
                 f'font-family="sans-serif" font-size="{h//7}" fill="{text_col}" opacity="0.9">{label}</text>')
    # Shine
    s.append(f'<ellipse cx="{x0+w*0.3:.0f}" cy="{y0+h*0.25:.0f}" '
             f'rx="{w*0.15:.0f}" ry="{h*0.08:.0f}" fill="white" opacity="0.25"/>')
    s.append('</g>')
    return '\n'.join(s)

# ── 6. DEFEATED CAT 1: on back, X eyes, at left ──────────
# Lying on back: head at (165, 255), body up, paws in air
cc = '#F5F0E8'   # defeated cat fur
oc = '#2a1505'   # outline color

# Body (ellipse lying horizontal)
parts.append(f'<ellipse cx="165" cy="268" rx="48" ry="32" fill="{cc}" stroke="{oc}" stroke-width="2"/>')
# Left paw up
parts.append(f'<ellipse cx="128" cy="238" rx="14" ry="10" fill="{cc}" stroke="{oc}" stroke-width="1.5" transform="rotate(-20,128,238)"/>')
# Right paw up
parts.append(f'<ellipse cx="202" cy="235" rx="14" ry="10" fill="{cc}" stroke="{oc}" stroke-width="1.5" transform="rotate(20,202,235)"/>')
# Tail curled
parts.append(f'<path d="M 200,278 Q 240,260 235,285 Q 228,305 210,295" fill="none" stroke="{cc}" stroke-width="10" stroke-linecap="round"/>')
parts.append(f'<path d="M 200,278 Q 240,260 235,285 Q 228,305 210,295" fill="none" stroke="{oc}" stroke-width="2" stroke-linecap="round"/>')
# Head (lying to the side, tilted)
parts.append(f'<g transform="rotate(-80, 148, 252)">')
parts.append(cat_face(148, 252, 36, xeyes=True, fur=f'url(#furDefeated)', shadow=f'url(#furShadow)',
                       blush=False, brow=False, lw=1.8, ear_col='#E8D8B8', ear_in='#FFB0C8'))
parts.append('</g>')
# X eyes text
parts.append(f'<text x="148" y="257" text-anchor="middle" font-size="22" fill="#2a1505" font-weight="bold">✕✕</text>')
# "KO" label
parts.append(f'<text x="162" y="310" text-anchor="middle" font-size="11" fill="#FF6060" font-weight="bold" opacity="0.9">K.O.</text>')
# Stars floating above
for i, (sx, sy, sr) in enumerate([(130,210,6),(158,202,7),(180,208,5)]):
    parts.append(star(sx, sy, sr, fill='#FFD040', opacity=0.85))

# ── 7. DEFEATED CAT 2: face down, at center-left ─────────
# Lying face down: showing head turned sideways + back/butt
parts.append(f'<ellipse cx="415" cy="272" rx="62" ry="24" fill="{cc}" stroke="{oc}" stroke-width="2"/>')
# Head turned sideways
parts.append(f'<g transform="rotate(90, 360, 268)">')
parts.append(cat_face(360, 268, 30, closed=True, fur=f'url(#furDefeated)', shadow=f'url(#furShadow)',
                       blush=False, brow=False, lw=1.8, ear_col='#E8D8B8', ear_in='#FFB0C8'))
parts.append('</g>')
# Tail (horizontal, visible)
parts.append(f'<path d="M 468,270 Q 500,255 510,270 Q 520,285 505,290" fill="none" stroke="{cc}" stroke-width="9" stroke-linecap="round"/>')
parts.append(f'<path d="M 468,270 Q 500,255 510,270 Q 520,285 505,290" fill="none" stroke="{oc}" stroke-width="1.8" stroke-linecap="round"/>')
# Tear drops
for tx, ty in [(340,238),(333,248)]:
    parts.append(f'<ellipse cx="{tx}" cy="{ty}" rx="4" ry="6" fill="#80C0FF" opacity="0.85" transform="rotate(10,{tx},{ty})"/>')
# Cards fallen around
parts.append(card_svg(380, 250, 28, 40, 'fire', angle=25))
parts.append(card_svg(435, 255, 28, 40, 'wind', angle=-15))

# ── 8. DEFEATED CAT 3: dizzy slumped, center ─────────────
# Sitting slumped, head drooped forward
# Body
parts.append(f'<ellipse cx="658" cy="272" rx="38" ry="36" fill="{cc}" stroke="{oc}" stroke-width="2"/>')
# Head drooped (slightly rotated forward/down)
parts.append(f'<g transform="rotate(15, 655, 238)">')
parts.append(cat_face(655, 238, 34, swirl=True, fur=f'url(#furDefeated)', shadow=f'url(#furShadow)',
                       blush=False, brow=False, lw=1.8, ear_col='#E8D8B8', ear_in='#FFB0C8'))
parts.append('</g>')
# Swirly eyes override
parts.append(f'<text x="655" y="241" text-anchor="middle" font-size="19" fill="#2a1505">@_@</text>')
# Tiny cross/veins on head (frustration)
parts.append(f'<text x="685" y="215" font-size="18" fill="#FF4040" opacity="0.9">💢</text>')
# Cards scattered
parts.append(card_svg(620, 260, 26, 36, 'water', angle=-30))
parts.append(card_svg(690, 258, 26, 36, 'earth', angle=20))
parts.append(card_svg(635, 285, 24, 34, 'fire',  angle=50))
# Drop sweat
parts.append(f'<path d="M 620,220 Q 615,230 618,238 Q 622,245 625,235 Z" fill="#A0C8FF" opacity="0.85"/>')

# ── 9. MAIN CAT CHARACTER (triumphant) ───────────────────
#
#  Position: right side, cx≈1015 cy_head≈120
#  Standing confidently with card fan in left paw
#
MX, MY = 1010, 118   # head center
MR = 74              # head radius

# Glow aura around main cat
parts.append(f'<ellipse cx="{MX}" cy="{MY+70}" rx="180" ry="155" fill="#FFD060" opacity="0.07" filter="url(#bigGlow)"/>')

# TAIL (behind body, sweep to the right and up)
parts.append(f'<path d="M {MX+50} {MY+200} Q {MX+130} {MY+180} {MX+160} {MY+140} Q {MX+195} {MY+90} {MX+170} {MY+60}" '
             f'fill="none" stroke="{cc}" stroke-width="22" stroke-linecap="round"/>')
parts.append(f'<path d="M {MX+50} {MY+200} Q {MX+130} {MY+180} {MX+160} {MY+140} Q {MX+195} {MY+90} {MX+170} {MY+60}" '
             f'fill="none" stroke="#E0C898" stroke-width="16" stroke-linecap="round"/>')
parts.append(f'<path d="M {MX+50} {MY+200} Q {MX+130} {MY+180} {MX+160} {MY+140} Q {MX+195} {MY+90} {MX+170} {MY+60}" '
             f'fill="none" stroke="{oc}" stroke-width="2" stroke-linecap="round"/>')
# Tail tip floof
parts.append(f'<ellipse cx="{MX+168}" cy="{MY+54}" rx="18" ry="14" fill="white" stroke="{oc}" stroke-width="1.5" transform="rotate(-30,{MX+168},{MY+54})"/>')

# BODY (chibi proportion: body height ≈ 55% of head diameter)
BX, BY = MX, MY + MR + 65
parts.append(f'<ellipse cx="{BX}" cy="{BY}" rx="58" ry="70" fill="url(#cloakGrad)" stroke="{oc}" stroke-width="2.5"/>')
# Cloak highlight (left rim)
parts.append(f'<path d="M {BX-58} {BY-10} Q {BX-70} {BY+20} {BX-50} {BY+55}" '
             f'fill="none" stroke="#8060FF" stroke-width="5" opacity="0.5" stroke-linecap="round"/>')
# Gold trim belt
parts.append(f'<ellipse cx="{BX}" cy="{BY+10}" rx="56" ry="8" fill="none" stroke="#FFD040" stroke-width="3.5" opacity="0.85"/>')
# Card emblem on chest
parts.append(f'<text x="{BX}" y="{BY+8}" text-anchor="middle" font-size="20" fill="#FFD040" opacity="0.9">♦</text>')
# Neck/chest tuft (white fluffy)
parts.append(f'<ellipse cx="{MX}" cy="{MY+MR-2}" rx="22" ry="18" fill="white" stroke="{oc}" stroke-width="1.5"/>')

# LEGS (short chibi stubs)
for lx in [BX-30, BX+30]:
    parts.append(f'<ellipse cx="{lx}" cy="{BY+68}" rx="20" ry="14" fill="url(#cloakGrad)" stroke="{oc}" stroke-width="2"/>')
    # Paw
    parts.append(f'<ellipse cx="{lx}" cy="{BY+80}" rx="18" ry="10" fill="url(#furMain)" stroke="{oc}" stroke-width="1.8"/>')
    # Paw toes
    for t in [-7, 0, 7]:
        parts.append(f'<ellipse cx="{lx+t}" cy="{BY+86}" rx="5" ry="4" fill="url(#furMain)" stroke="{oc}" stroke-width="1.2"/>')

# RIGHT ARM (raised, triumphant gesture — pointing up)
# Arm from body right side upward
parts.append(f'<path d="M {MX+52} {BY-38} Q {MX+95} {BY-80} {MX+88} {BY-118}" '
             f'fill="none" stroke="url(#cloakGrad)" stroke-width="26" stroke-linecap="round"/>')
parts.append(f'<path d="M {MX+52} {BY-38} Q {MX+95} {BY-80} {MX+88} {BY-118}" '
             f'fill="none" stroke="{oc}" stroke-width="2.5" stroke-linecap="round"/>')
# Right paw at top
parts.append(f'<ellipse cx="{MX+86}" cy="{BY-125}" rx="20" ry="16" fill="url(#furMain)" stroke="{oc}" stroke-width="2" transform="rotate(-20,{MX+86},{BY-125})"/>')
# Pointing finger claw
parts.append(f'<line x1="{MX+90}" y1="{BY-138}" x2="{MX+96}" y2="{BY-152}" stroke="{oc}" stroke-width="3" stroke-linecap="round"/>')
# Victory sparkles from right paw
for sx, sy, sr in [(MX+110, BY-148, 7), (MX+100, BY-162, 5), (MX+120, BY-135, 4)]:
    parts.append(sparkle(sx, sy, sr, fill='#FFD040', opacity=0.9))
    parts.append(f'<circle cx="{sx}" cy="{sy}" r="3" fill="#FFD040" opacity="0.8"/>')

# LEFT ARM (extended left-down, holding card fan)
PAWN_X, PAWN_Y = MX - 105, BY - 60   # paw position (holding cards)
parts.append(f'<path d="M {MX-52} {BY-38} Q {PAWN_X+20} {BY-50} {PAWN_X+8} {PAWN_Y+8}" '
             f'fill="none" stroke="url(#cloakGrad)" stroke-width="26" stroke-linecap="round"/>')
parts.append(f'<path d="M {MX-52} {BY-38} Q {PAWN_X+20} {BY-50} {PAWN_X+8} {PAWN_Y+8}" '
             f'fill="none" stroke="{oc}" stroke-width="2.5" stroke-linecap="round"/>')
# Left paw
parts.append(f'<ellipse cx="{PAWN_X}" cy="{PAWN_Y+14}" rx="20" ry="15" fill="url(#furMain)" stroke="{oc}" stroke-width="2"/>')

# CARD FAN held in left paw (5 cards, fanned)
fan_cards = [
    ('fire',  '28', 'VER'),
    ('earth', '28', 'HAM'),
    ('wind',  '26', 'NOR'),
    ('water', '25', 'RUS'),
    ('gold',  '26', 'LEC'),
]
fan_cx, fan_cy = PAWN_X - 10, PAWN_Y - 25
angles = [-32, -16, 0, 16, 32]
for (elem, atk, lbl), ang in zip(fan_cards, angles):
    parts.append(card_svg(fan_cx, fan_cy, 38, 54, elem, label=lbl, atk=atk, angle=ang, rx=4))

# HEAD (drawn on top of body/arms)
parts.append(cat_face(MX, MY, MR, iris='url(#eyeIris)', fur='url(#furMain)',
                       shadow='url(#furShadow)', brow=True, blush=True, lw=2.5))

# Extra detail: small crown/star on head
parts.append(star(MX, MY - MR - 10, 14, n=5, fill='#FFD040', opacity=0.95))
parts.append(f'<circle cx="{MX}" cy="{MY-MR-10}" r="5" fill="#FFF0A0" opacity="0.95"/>')

# Floating sparkles around main cat
for px, py, pr in [(MX-90, MY+30, 5),(MX+200, MY+80, 6),(MX-70, MY+130, 4),
                    (MX+180, MY+160, 5),(MX-40, MY-50, 4),(MX+130, MY-30, 5)]:
    parts.append(sparkle(px, py, pr, fill='#A0E8FF', opacity=0.7))

# ── 10. TEXT OVERLAY ───────────────────────────────────────
# Position text on the left side where there's open space
TX = 100   # left anchor for text

# Backing glow for title
parts.append(f'<ellipse cx="340" cy="95" rx="300" ry="80" fill="#2a1868" opacity="0.55"/>')

# "SEASON CHALLENGE!" — big white title
for dx, dy in [(-2,2),(2,2),(-2,-2),(2,-2),(0,3)]:
    parts.append(f'<text x="{TX+dx}" y="{90+dy}" font-family="Impact,Arial Black,sans-serif" '
                 f'font-size="54" fill="#000000" opacity="0.5" font-weight="900" letter-spacing="2">SEASON CHALLENGE!</text>')
parts.append(f'<text x="{TX}" y="90" font-family="Impact,Arial Black,sans-serif" '
             f'font-size="54" fill="#FFFFFF" font-weight="900" letter-spacing="2">SEASON CHALLENGE!</text>')

# Prize line — gold
for dx, dy in [(-2,2),(2,2),(0,3)]:
    parts.append(f'<text x="{TX+dx}" y="{148+dy}" font-family="Impact,Arial Black,sans-serif" '
                 f'font-size="42" fill="#7A4200" opacity="0.7" font-weight="900">10,000,000 PIT</text>')
parts.append(f'<text x="{TX}" y="148" font-family="Impact,Arial Black,sans-serif" '
             f'font-size="42" fill="#FFD700" font-weight="900">10,000,000 PIT</text>')

# Subtitle
parts.append(f'<text x="{TX}" y="178" font-family="Arial,sans-serif" font-size="14" '
             f'fill="#A0B8D8" letter-spacing="2">BIGGEST POT EVER  ·  LIMITED TIME  ·  WINNER TAKES ALL</text>')

# Limited-time badge
parts.append(f'<rect x="{TX}" y="192" width="210" height="26" rx="13" fill="#FF3060" opacity="0.9"/>')
parts.append(f'<text x="{TX+105}" y="210" text-anchor="middle" font-family="Arial,sans-serif" '
             f'font-size="12" fill="white" font-weight="bold" letter-spacing="2">▶ 立即挑戰 ◀</text>')

# ── 11. CLOSE SVG ─────────────────────────────────────────
parts.append('</svg>')

svg_content = '\n'.join(parts)

cairosvg.svg2png(
    bytestring=svg_content.encode('utf-8'),
    write_to='/opt/battle_arena/static/hero_banner.png',
    output_width=W, output_height=H,
    scale=2,          # render at 2× then scale down for anti-aliasing
)
print(f'Saved hero_banner.png  {W}x{H}')
