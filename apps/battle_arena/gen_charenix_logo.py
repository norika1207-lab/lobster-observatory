"""Charenix — brand logo (Phoenix chaos card battle)"""
import cairosvg, math

# ── Full horizontal logo (header use): 480x120 ──
# ── Square icon (favicon / standalone): 200x200 ──

def hex_pts(cx, cy, r, rot=90):
    pts = []
    for i in range(6):
        a = math.radians(60*i + rot)
        pts.append((cx + r*math.cos(a), cy + r*math.sin(a)))
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

def orbit(cx, cy, r, n=20):
    lines = []
    for i in range(n):
        a = math.radians(360/n * i)
        ox, oy = cx + r*math.cos(a), cy + r*math.sin(a)
        big = i % 4 == 0
        lines.append(f'<circle cx="{ox:.1f}" cy="{oy:.1f}" r="{3.5 if big else 1.8}" '
                     f'fill="#C060FF" opacity="{0.85 if big else 0.3}"/>')
    return "\n".join(lines)

# ════════════════════════════════════════════════
#  SQUARE ICON  200×200
# ════════════════════════════════════════════════
ICX, ICY = 100, 100

icon_svg = f'''<svg width="200" height="200" xmlns="http://www.w3.org/2000/svg">
<defs>
  <radialGradient id="bg" cx="50%" cy="50%" r="60%">
    <stop offset="0%"   stop-color="#1E0A40"/>
    <stop offset="100%" stop-color="#080412"/>
  </radialGradient>
  <linearGradient id="hexG" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%"   stop-color="#CC44FF"/>
    <stop offset="50%"  stop-color="#FF6820"/>
    <stop offset="100%" stop-color="#FF3800"/>
  </linearGradient>
  <linearGradient id="flameG" x1="0" y1="1" x2="0.4" y2="0">
    <stop offset="0%"   stop-color="#FF4400"/>
    <stop offset="40%"  stop-color="#FF9020"/>
    <stop offset="80%"  stop-color="#FFCC44"/>
    <stop offset="100%" stop-color="#FFF0A0"/>
  </linearGradient>
  <linearGradient id="wingG" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%"   stop-color="#9020FF"/>
    <stop offset="100%" stop-color="#FF6820"/>
  </linearGradient>
  <filter id="glow" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="6" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="softglow" x="-60%" y="-60%" width="220%" height="220%">
    <feGaussianBlur stdDeviation="10"/>
  </filter>
</defs>

<rect width="200" height="200" fill="url(#bg)"/>

<!-- Ambient glow -->
<ellipse cx="100" cy="105" rx="70" ry="65" fill="#8020FF" opacity="0.12" filter="url(#softglow)"/>
<ellipse cx="100" cy="108" rx="50" ry="45" fill="#FF5010" opacity="0.10" filter="url(#softglow)"/>

<!-- Orbit dots -->
{orbit(ICX, ICY, 88, 18)}

<!-- Outer hex glow -->
<polygon points="{hex_pts(ICX,ICY,80)}" fill="none" stroke="#AA30FF" stroke-width="10" opacity="0.15" filter="url(#softglow)"/>
<!-- Outer hex -->
<polygon points="{hex_pts(ICX,ICY,80)}" fill="none" stroke="url(#hexG)" stroke-width="2.2" filter="url(#glow)"/>
<!-- Inner hex -->
<polygon points="{hex_pts(ICX,ICY,60)}" fill="rgba(15,5,30,0.85)" stroke="#8030CC" stroke-width="1" stroke-dasharray="6 5"/>

<!-- ── PHOENIX FLAME MARK ── -->
<!-- Base flame (wide) -->
<path d="M 100,132 C 68,120 62,95 72,72 C 78,60 85,56 88,62 C 90,67 86,76 90,82
         C 92,86 96,82 97,76 C 99,68 96,56 100,46
         C 104,56 101,68 103,76 C 104,82 108,86 110,82
         C 114,76 110,67 112,62 C 115,56 122,60 128,72
         C 138,95 132,120 100,132 Z"
  fill="url(#flameG)" filter="url(#glow)"/>

<!-- Wing sweep left -->
<path d="M 88,90 C 78,85 65,88 58,78 C 68,74 80,76 88,82 Z"
  fill="url(#wingG)" opacity="0.85"/>
<!-- Wing sweep right -->
<path d="M 112,90 C 122,85 135,88 142,78 C 132,74 120,76 112,82 Z"
  fill="url(#wingG)" opacity="0.85"/>

<!-- Bright core -->
<ellipse cx="100" cy="89" rx="10" ry="14" fill="#FFF5C0" opacity="0.7" filter="url(#glow)"/>

<!-- Vertex connector lines -->
<line x1="{hex_pts(ICX,ICY,80).split()[0].split(',')[0]}" y1="{hex_pts(ICX,ICY,80).split()[0].split(',')[1]}"
      x2="{hex_pts(ICX,ICY,60).split()[0].split(',')[0]}" y2="{hex_pts(ICX,ICY,60).split()[0].split(',')[1]}"
      stroke="#CC44FF" stroke-width="1" opacity="0.5"/>
</svg>'''

cairosvg.svg2png(bytestring=icon_svg.encode(), write_to='/opt/battle_arena/static/charenix_icon.png', scale=2)
print("icon ok")

# ════════════════════════════════════════════════
#  HORIZONTAL LOGO  600×130  (header)
# ════════════════════════════════════════════════
LW, LH = 600, 130
LCX, LCY = 62, 65

logo_svg = f'''<svg width="{LW}" height="{LH}" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="bg2" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%"   stop-color="#12062A"/>
    <stop offset="100%" stop-color="#08040E" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="hexG2" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%"   stop-color="#CC44FF"/>
    <stop offset="50%"  stop-color="#FF6820"/>
    <stop offset="100%" stop-color="#FF3800"/>
  </linearGradient>
  <linearGradient id="flameG2" x1="0" y1="1" x2="0.4" y2="0">
    <stop offset="0%"   stop-color="#FF4400"/>
    <stop offset="40%"  stop-color="#FF9020"/>
    <stop offset="80%"  stop-color="#FFCC44"/>
    <stop offset="100%" stop-color="#FFF0A0"/>
  </linearGradient>
  <linearGradient id="nameG" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%"   stop-color="#FFFFFF"/>
    <stop offset="45%"  stop-color="#E8D0FF"/>
    <stop offset="100%" stop-color="#CC88FF"/>
  </linearGradient>
  <linearGradient id="tagG" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%"   stop-color="#FF7020"/>
    <stop offset="100%" stop-color="#FFAA30"/>
  </linearGradient>
  <filter id="glow2" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="4" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="sg" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur stdDeviation="8"/>
  </filter>
  <filter id="textglow" x="-10%" y="-40%" width="120%" height="180%">
    <feGaussianBlur stdDeviation="3" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>

<rect width="{LW}" height="{LH}" fill="url(#bg2)"/>

<!-- Icon: hex frame -->
<ellipse cx="{LCX}" cy="{LCY}" rx="52" ry="48" fill="#8020FF" opacity="0.10" filter="url(#sg)"/>
<ellipse cx="{LCX}" cy="{LCY+4}" rx="38" ry="34" fill="#FF5010" opacity="0.09" filter="url(#sg)"/>

<polygon points="{hex_pts(LCX,LCY,51)}" fill="none" stroke="#9030EE" stroke-width="7" opacity="0.18" filter="url(#sg)"/>
<polygon points="{hex_pts(LCX,LCY,51)}" fill="none" stroke="url(#hexG2)" stroke-width="1.8" filter="url(#glow2)"/>
<polygon points="{hex_pts(LCX,LCY,38)}" fill="rgba(14,5,28,0.9)" stroke="#7025BB" stroke-width="1" stroke-dasharray="5 4"/>

<!-- Phoenix flame (small, centered in hex) -->
<path d="M {LCX},{LCY+22}
  C {LCX-20},{LCY+14} {LCX-22},{LCY-2} {LCX-15},{LCY-16}
  C {LCX-11},{LCY-22} {LCX-6},{LCY-24} {LCX-4},{LCY-20}
  C {LCX-3},{LCY-17} {LCX-5},{LCY-11} {LCX-3},{LCY-7}
  C {LCX-1},{LCY-3} {LCX+1},{LCY-5} {LCX+2},{LCY-10}
  C {LCX+3},{LCY-16} {LCX+1},{LCY-23} {LCX},{LCY-30}
  C {LCX+4},{LCY-20} {LCX+2},{LCY-12} {LCX+4},{LCY-7}
  C {LCX+6},{LCY-2} {LCX+8},{LCY-4} {LCX+8},{LCY-10}
  C {LCX+9},{LCY-17} {LCX+6},{LCY-23} {LCX+9},{LCY-26}
  C {LCX+15},{LCY-22} {LCX+18},{LCY-14} {LCX+16},{LCY-4}
  C {LCX+22},{LCY+2} {LCX+20},{LCY+16} {LCX},{LCY+22} Z"
  fill="url(#flameG2)" filter="url(#glow2)"/>

<!-- Wing hints -->
<path d="M {LCX-4},{LCY} C {LCX-14},{LCY-5} {LCX-25},{LCY-2} {LCX-30},{LCY-10} C {LCX-22},{LCY-12} {LCX-12},{LCY-8} {LCX-4},{LCY-4} Z"
  fill="#9030FF" opacity="0.7"/>
<path d="M {LCX+4},{LCY} C {LCX+14},{LCY-5} {LCX+25},{LCY-2} {LCX+30},{LCY-10} C {LCX+22},{LCY-12} {LCX+12},{LCY-8} {LCX+4},{LCY-4} Z"
  fill="#9030FF" opacity="0.7"/>

<!-- Bright core -->
<ellipse cx="{LCX}" cy="{LCY-8}" rx="7" ry="10" fill="#FFF0A0" opacity="0.65" filter="url(#glow2)"/>

<!-- ── Text: CHARENIX ── -->
<!-- Shadow -->
<text x="137" y="80" font-family="'Arial Black','Impact',sans-serif"
  font-weight="900" font-size="56" letter-spacing="4"
  fill="#3A0870" opacity="0.8">CHARENIX</text>
<!-- Main text -->
<text x="135" y="78" font-family="'Arial Black','Impact',sans-serif"
  font-weight="900" font-size="56" letter-spacing="4"
  fill="url(#nameG)" filter="url(#textglow)">CHARENIX</text>

<!-- Divider line -->
<line x1="136" y1="88" x2="565" y2="88" stroke="url(#hexG2)" stroke-width="1" opacity="0.5"/>

<!-- Tagline -->
<text x="137" y="107" font-family="'Share Tech Mono','Courier New',monospace"
  font-size="12" letter-spacing="4" fill="url(#tagG)" opacity="0.9">CROSS-SPORT CARD BATTLE ARENA</text>
</svg>'''

cairosvg.svg2png(bytestring=logo_svg.encode(), write_to='/opt/battle_arena/static/charenix_logo.png', scale=2)
print("horizontal logo ok")
print("Files: charenix_icon.png, charenix_logo.png")
