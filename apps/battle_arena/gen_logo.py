"""Claude Code — logo generator"""
import cairosvg, math

W, H = 520, 580
CX, CY = 260, 235

def hex_points(cx, cy, r, rotation=0):
    pts = []
    for i in range(6):
        a = math.radians(60 * i + rotation)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts

def pts_str(pts):
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)

def arc_path(cx, cy, r, start_deg, end_deg, sweep=1):
    """SVG arc from start_deg to end_deg."""
    s = math.radians(start_deg)
    e = math.radians(end_deg)
    x1, y1 = cx + r*math.cos(s), cy + r*math.sin(s)
    x2, y2 = cx + r*math.cos(e), cy + r*math.sin(e)
    large = 1 if abs(end_deg - start_deg) > 180 else 0
    return f"M {x1:.2f},{y1:.2f} A {r},{r} 0 {large},{sweep} {x2:.2f},{y2:.2f}"

# Outer, mid, inner hexagons
R1, R2, R3 = 170, 140, 108
hex1 = hex_points(CX, CY, R1, rotation=90)   # pointy-top
hex2 = hex_points(CX, CY, R2, rotation=90)
hex3 = hex_points(CX, CY, R3, rotation=90)

# Segment connector lines (hex1 corner → hex2 corner)
seg_lines = "\n".join(
    f'<line x1="{hex1[i][0]:.1f}" y1="{hex1[i][1]:.1f}" '
    f'x2="{hex2[i][0]:.1f}" y2="{hex2[i][1]:.1f}" '
    f'stroke="#FF8040" stroke-width="1" opacity="0.55"/>'
    for i in range(6)
)

# Dots at hex1 vertices
vertex_dots = "\n".join(
    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#FF9050" opacity="0.9"/>'
    for x, y in hex1
)
vertex_dots_glow = "\n".join(
    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="#FF6030" opacity="0.3" filter="url(#vdotGlow)"/>'
    for x, y in hex1
)

# Tick marks along hex1 edges (midpoints)
tick_marks = []
for i in range(6):
    ax, ay = hex1[i]
    bx, by = hex1[(i+1) % 6]
    mx, my = (ax+bx)/2, (ay+by)/2
    # normal direction (inward)
    nx = CX - mx; ny = CY - my
    ln = math.sqrt(nx*nx + ny*ny)
    nx, ny = nx/ln * 8, ny/ln * 8
    tick_marks.append(
        f'<line x1="{mx:.1f}" y1="{my:.1f}" x2="{mx+nx:.1f}" y2="{my+ny:.1f}" '
        f'stroke="#FF8040" stroke-width="1.5" opacity="0.6"/>'
    )
tick_str = "\n".join(tick_marks)

# Orbital dot ring (small dots around CY at r=195)
orbit_dots = []
for i in range(24):
    a = math.radians(15 * i)
    ox = CX + 197 * math.cos(a)
    oy = CY + 197 * math.sin(a)
    op = 0.7 if i % 3 == 0 else 0.25
    r  = 3.5 if i % 3 == 0 else 1.8
    orbit_dots.append(f'<circle cx="{ox:.1f}" cy="{oy:.1f}" r="{r}" fill="#FF9050" opacity="{op}"/>')
orbit_str = "\n".join(orbit_dots)

# ── The C> symbol ──────────────────────────────────────────
# Thick C arc (outer r=72, inner r=42, opening on right side)
# Arc from 145° to 215° leave gap on right (35° on each side)
C_OUTER, C_INNER = 72, 42
def ring_arc(cx, cy, r_out, r_in, a_start, a_end):
    """Closed path for a thick arc (ring segment)."""
    # Outer arc: a_start → a_end clockwise (sweep=1)
    # Inner arc: a_end → a_start clockwise (sweep=0 = counter)
    s, e = math.radians(a_start), math.radians(a_end)
    ox1, oy1 = cx + r_out*math.cos(s), cy + r_out*math.sin(s)
    ox2, oy2 = cx + r_out*math.cos(e), cy + r_out*math.sin(e)
    ix1, iy1 = cx + r_in*math.cos(e),  cy + r_in*math.sin(e)
    ix2, iy2 = cx + r_in*math.cos(s),  cy + r_in*math.sin(s)
    span = abs(a_end - a_start)
    large = 1 if span > 180 else 0
    return (f"M {ox1:.2f},{oy1:.2f} "
            f"A {r_out},{r_out} 0 {large},1 {ox2:.2f},{oy2:.2f} "
            f"L {ix1:.2f},{iy1:.2f} "
            f"A {r_in},{r_in} 0 {large},0 {ix2:.2f},{iy2:.2f} Z")

c_path = ring_arc(CX - 12, CY, C_OUTER, C_INNER, 145, 215)

# Rounded end caps on C
def end_cap(cx, cy, r_out, r_in, angle_deg):
    a = math.radians(angle_deg)
    ox, oy = cx + r_out*math.cos(a), cy + r_out*math.sin(a)
    ix, iy = cx + r_in*math.cos(a),  cy + r_in*math.sin(a)
    mid_r = (r_out + r_in) / 2
    cap_r = (r_out - r_in) / 2
    mcx, mcy = cx + mid_r*math.cos(a), cy + mid_r*math.sin(a)
    return f'<circle cx="{mcx:.2f}" cy="{mcy:.2f}" r="{cap_r:.2f}" fill="url(#symbolGrad)"/>'

cap_top = end_cap(CX - 12, CY, C_OUTER, C_INNER, 145)
cap_bot = end_cap(CX - 12, CY, C_OUTER, C_INNER, 215)

# > arrow (chevron, to the right of center)
AX, AY = CX + 38, CY   # arrow tip x
AS = 34                  # half-height
AT = 22                  # arrow depth
arrow_path = (f"M {AX:.1f},{AY:.1f} "
              f"L {AX-AT:.1f},{AY-AS:.1f} "
              f"M {AX:.1f},{AY:.1f} "
              f"L {AX-AT:.1f},{AY+AS:.1f}")

# underscore/cursor beneath > (terminal cursor blink effect)
# SVG animate for blink
cursor_x = AX - AT + 4
cursor_y = AY + AS + 14

svg = f'''<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <!-- Deep space background -->
  <radialGradient id="bgGrad" cx="50%" cy="42%" r="58%">
    <stop offset="0%"   stop-color="#141C38"/>
    <stop offset="55%"  stop-color="#0C1020"/>
    <stop offset="100%" stop-color="#060810"/>
  </radialGradient>

  <!-- Hex stroke gradient -->
  <linearGradient id="hexGrad" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%"   stop-color="#FFB347"/>
    <stop offset="40%"  stop-color="#FF7B2A"/>
    <stop offset="100%" stop-color="#FF4500"/>
  </linearGradient>
  <linearGradient id="hex2Grad" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%"   stop-color="#FF8C42" stop-opacity="0.6"/>
    <stop offset="100%" stop-color="#FF4500" stop-opacity="0.2"/>
  </linearGradient>

  <!-- Symbol gradient -->
  <linearGradient id="symbolGrad" x1="0" y1="0" x2="0.5" y2="1">
    <stop offset="0%"   stop-color="#FFCC55"/>
    <stop offset="45%"  stop-color="#FF8C35"/>
    <stop offset="100%" stop-color="#FF4520"/>
  </linearGradient>

  <!-- Arrow gradient -->
  <linearGradient id="arrowGrad" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%"   stop-color="#FFFFFF"/>
    <stop offset="100%" stop-color="#FFB060"/>
  </linearGradient>

  <!-- Inner hex fill -->
  <radialGradient id="innerFill" cx="40%" cy="35%" r="70%">
    <stop offset="0%"   stop-color="#1A2240"/>
    <stop offset="100%" stop-color="#08090F"/>
  </radialGradient>

  <!-- Glow filters -->
  <filter id="hexGlow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="10" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="symbolGlow" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="7" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="vdotGlow" x="-200%" y="-200%" width="500%" height="500%">
    <feGaussianBlur stdDeviation="5"/>
  </filter>
  <filter id="bgGlow" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="22"/>
  </filter>
  <filter id="textGlow" x="-20%" y="-60%" width="140%" height="220%">
    <feGaussianBlur stdDeviation="4" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="subtleGlow" x="-20%" y="-60%" width="140%" height="220%">
    <feGaussianBlur stdDeviation="2" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>

<!-- ── BACKGROUND ── -->
<rect width="{W}" height="{H}" fill="url(#bgGrad)"/>

<!-- Background ambient glow (warm orange below hex) -->
<ellipse cx="{CX}" cy="{CY+30}" rx="155" ry="130" fill="#FF6020" opacity="0.08" filter="url(#bgGlow)"/>

<!-- ── ORBIT RING ── -->
{orbit_str}

<!-- ── HEX LAYER 1: outer glow halo ── -->
<polygon points="{pts_str(hex1)}"
  fill="none" stroke="#FF5510" stroke-width="14" opacity="0.15" filter="url(#bgGlow)"/>

<!-- ── HEX LAYER 2: outer hex border ── -->
<polygon points="{pts_str(hex1)}"
  fill="none" stroke="url(#hexGrad)" stroke-width="2.8" filter="url(#hexGlow)"/>

<!-- Vertex accent dots + glow -->
{vertex_dots_glow}
{vertex_dots}

<!-- Segment lines & tick marks -->
{seg_lines}
{tick_str}

<!-- ── HEX LAYER 3: mid hex (dashed) ── -->
<polygon points="{pts_str(hex2)}"
  fill="none" stroke="url(#hex2Grad)" stroke-width="1.2"
  stroke-dasharray="8 6"/>

<!-- ── HEX LAYER 4: inner hex fill ── -->
<polygon points="{pts_str(hex3)}"
  fill="url(#innerFill)" stroke="#FF8040" stroke-width="1.2" opacity="0.85"/>

<!-- Inner hex subtle glow rim -->
<polygon points="{pts_str(hex3)}"
  fill="none" stroke="#FF6020" stroke-width="6" opacity="0.12" filter="url(#bgGlow)"/>

<!-- ── CENTRE MARK: C> symbol ── -->
<!-- C arc glow layer -->
<path d="{c_path}" fill="#FF6020" opacity="0.35" filter="url(#symbolGlow)"/>
<!-- C arc main -->
<path d="{c_path}" fill="url(#symbolGrad)" filter="url(#symbolGlow)"/>
<!-- Rounded end caps -->
{cap_top}
{cap_bot}

<!-- > chevron arrow -->
<path d="{arrow_path}"
  fill="none" stroke="url(#arrowGrad)" stroke-width="13"
  stroke-linecap="round" stroke-linejoin="round"
  filter="url(#symbolGlow)"/>
<!-- > inner bright -->
<path d="{arrow_path}"
  fill="none" stroke="white" stroke-width="4"
  stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>

<!-- Cursor underscore (terminal feel) -->
<rect x="{cursor_x:.0f}" y="{cursor_y:.0f}" width="24" height="5" rx="2"
  fill="#FF9050" opacity="0.85"/>

<!-- ── FINE DETAIL: cross-hairs inside inner hex ── -->
<line x1="{CX-R3+10:.0f}" y1="{CY}" x2="{CX+R3-10:.0f}" y2="{CY}"
  stroke="#FF6030" stroke-width="0.6" opacity="0.2"/>
<line x1="{CX}" y1="{CY-R3+10:.0f}" x2="{CX}" y2="{CY+R3-10:.0f}"
  stroke="#FF6030" stroke-width="0.6" opacity="0.2"/>

<!-- ── TEXT ── -->
<!-- "CLAUDE" — large white with subtle glow -->
<text x="{CX}" y="438"
  text-anchor="middle"
  font-family="'Arial Black', 'Impact', sans-serif"
  font-weight="900" font-size="58" letter-spacing="10"
  fill="white" filter="url(#subtleGlow)">CLAUDE</text>

<!-- "CODE" — orange, slightly smaller, tight tracking -->
<text x="{CX}" y="487"
  text-anchor="middle"
  font-family="'Arial Black', 'Impact', sans-serif"
  font-weight="900" font-size="42" letter-spacing="18"
  fill="url(#symbolGrad)" filter="url(#textGlow)">CODE</text>

<!-- Divider line under CLAUDE -->
<line x1="{CX-120}" y1="449" x2="{CX+120}" y2="449"
  stroke="url(#hexGrad)" stroke-width="1.2" opacity="0.6"/>

<!-- Tagline -->
<text x="{CX}" y="530"
  text-anchor="middle"
  font-family="'Courier New', monospace"
  font-size="13" letter-spacing="4"
  fill="#6080A8" opacity="0.85">AI CODING ASSISTANT</text>

<!-- Version dots decoration -->
<circle cx="{CX-145}" cy="526" r="3" fill="#FF7030" opacity="0.6"/>
<circle cx="{CX+145}" cy="526" r="3" fill="#FF7030" opacity="0.6"/>
<line x1="{CX-138}" y1="526" x2="{CX-80}" y2="526" stroke="#FF6020" stroke-width="0.8" opacity="0.35"/>
<line x1="{CX+80}"  y1="526" x2="{CX+138}" y2="526" stroke="#FF6020" stroke-width="0.8" opacity="0.35"/>

</svg>'''

cairosvg.svg2png(
    bytestring=svg.encode('utf-8'),
    write_to='/opt/battle_arena/static/claude_logo.png',
    scale=2
)
print(f"Saved claude_logo.png  {W}x{H} (@2x = {W*2}x{H*2})")
