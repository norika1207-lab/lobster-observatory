#!/usr/bin/env python3
"""
龍蝦投注完整報告 - Excel 生成器
數據來源: /opt/battle_arena/app/battle_arena.db -> lobster_bet_decision_settlements
"""
import sqlite3
import json
import re
from datetime import datetime
from collections import defaultdict
import openpyxl
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side
)
from openpyxl.utils import get_column_letter

DB_PATH = "/opt/battle_arena/app/battle_arena.db"
OUTPUT_PATH = "/root/lobster_bet_report.xlsx"

SITE_TO_SPORT = {
    "dunknix.com": "籃球",
    "knocknix.com": "格鬥",
    "pitchnix.com": "棒球",
    "ragnovex.com": "電競",
    "turfenix.com": "賽馬",
    "throttenix.com": "賽車",
    "charenix.com/raid": "卡牌對戰",
}

SITE_TO_SPORT_EN = {
    "dunknix.com": "Basketball",
    "knocknix.com": "Fighting",
    "pitchnix.com": "Baseball",
    "ragnovex.com": "Esports",
    "turfenix.com": "HorseRacing",
    "throttenix.com": "Racing",
    "charenix.com/raid": "CardBattle",
}

PERSONA_DISPLAY = {
    "norika_oda": "norika_oda (主控/節奏)",
    "clawtrix": "clawtrix (數據/冷析)",
    "pincerbot": "pincerbot (規則/格鬥)",
    "ragclaw": "ragclaw (即時/電競)",
    "snaplex": "snaplex (卡牌/架構)",
    "hexclaw": "hexclaw (籃球/共識)",
    "vortexiq": "vortexiq (週期/趨勢)",
    "prismbit": "prismbit (棒球/理性)",
    "stonefang": "stonefang (診斷/硬邏輯)",
    "blazepaw": "blazepaw (火力/防衛)",
    "basaltrook": "basaltrook",
    "brineledger": "brineledger",
    "emberling": "emberling",
    "flarecub": "flarecub",
    "gauntlet": "gauntlet",
    "mirrorcalm": "mirrorcalm",
    "nullpinch": "nullpinch",
    "omenwake": "omenwake",
    "rushfin": "rushfin",
    "sparkrill": "sparkrill",
}

# Colors
COLORS = {
    "header_bg": "1A1A2E",
    "header_fg": "E0E0E0",
    "win_bg": "1B5E20",
    "loss_bg": "B71C1C",
    "void_bg": "424242",
    "cat_esports": "0D47A1",
    "cat_basketball": "E65100",
    "cat_baseball": "1B5E20",
    "cat_fighting": "880E4F",
    "cat_horse": "4A148C",
    "cat_racing": "BF360C",
    "cat_card": "006064",
    "row_even": "F5F5F5",
    "row_odd": "FFFFFF",
    "summary_header": "263238",
    "win_text": "4CAF50",
    "loss_text": "F44336",
}

CAT_COLORS = {
    "電競": "1565C0",
    "籃球": "E65100",
    "棒球": "2E7D32",
    "格鬥": "880E4F",
    "賽馬": "4A148C",
    "賽車": "BF360C",
    "卡牌對戰": "006064",
}

def extract_reason(reasoning):
    """Extract short reason from long reasoning text."""
    if not reasoning:
        return ""
    # Try to extract the 理由 part
    match = re.search(r'理由[：:]\s*(.{10,200}?)(?:；|$|\n)', reasoning)
    if match:
        text = match.group(1).strip()
        # Remove MBTI tags like [local][INTJ]
        text = re.sub(r'\[local\]\[[A-Z]+\]\s*', '', text)
        return text[:200]
    # Fallback: first 200 chars
    return reasoning[:200]

def get_short_reason(reasoning):
    """Very short reason for column."""
    text = extract_reason(reasoning)
    return text[:150] if text else ""

def border_thin():
    thin = Side(style='thin', color='CCCCCC')
    return Border(left=thin, right=thin, top=thin, bottom=thin)

def style_header_cell(cell, bg_color="1A1A2E", fg_color="FFFFFF", bold=True):
    cell.fill = PatternFill(fill_type="solid", fgColor=bg_color)
    cell.font = Font(bold=bold, color=fg_color, size=10)
    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell.border = border_thin()

def style_data_cell(cell, bg_color="FFFFFF", bold=False, wrap=False, align='left'):
    cell.fill = PatternFill(fill_type="solid", fgColor=bg_color)
    cell.font = Font(bold=bold, color="212121", size=9)
    cell.alignment = Alignment(horizontal=align, vertical='center', wrap_text=wrap)
    cell.border = border_thin()

def load_all_bets():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            persona, site, bet_id, event_key, event_title, pick, stake,
            confidence, outcome, status, payout, settled_at,
            raw_decision_reasoning, pre_bet_reasoning, integrated_reasoning,
            source_bet_json
        FROM lobster_bet_decision_settlements
        ORDER BY settled_at ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def load_card_bets():
    """Load card battle bets from memory JSON files."""
    import glob
    from pathlib import Path
    skip = {'deleted-lobsters', 'system', 'norika', 'elder'}
    card_bets = []
    for fp in sorted(glob.glob('/root/lobster-memory/*.json')):
        name = Path(fp).stem
        if name in skip:
            continue
        try:
            d = json.load(open(fp))
            for b in d.get('bets', []):
                if b.get('category') in ('card_battle', 'card_battle_raid'):
                    card_bets.append({
                        'persona': name,
                        'site': 'charenix.com/raid',
                        'bet_id': b.get('bet_id') or '',
                        'event_key': b.get('pattern_used') or '',
                        'event_title': b.get('category') or 'Card Battle',
                        'pick': b.get('confidence') or '',
                        'stake': b.get('stake') or 0,
                        'confidence': b.get('confidence') or '',
                        'outcome': b.get('outcome') or 'pending',
                        'status': b.get('outcome') or 'pending',
                        'payout': 0,
                        'settled_at': b.get('time') or '',
                        'raw_decision_reasoning': b.get('reasoning') or '',
                        'pre_bet_reasoning': '',
                        'integrated_reasoning': '',
                        'source_bet_json': json.dumps(b),
                    })
        except:
            pass
    return card_bets

def enrich_bets(rows):
    enriched = []
    for r in rows:
        site = r.get('site', '')
        sport = SITE_TO_SPORT.get(site, site)

        # Parse source_bet_json for odds
        odds = ''
        try:
            sj = json.loads(r.get('source_bet_json') or '{}')
            odds = str(sj.get('odds', ''))
        except:
            pass

        # Get short reason
        reason = get_short_reason(r.get('raw_decision_reasoning') or r.get('integrated_reasoning') or '')

        # Win/Loss/Void
        outcome = r.get('outcome', '').lower()
        is_win = outcome == 'win'
        is_loss = outcome == 'loss'

        enriched.append({
            'persona': r.get('persona', ''),
            'site': site,
            'sport': sport,
            'event_title': r.get('event_title') or r.get('event_key') or '',
            'pick': r.get('pick') or '',
            'stake': r.get('stake') or 0,
            'odds': odds,
            'confidence': r.get('confidence') or '',
            'outcome': outcome,
            'payout': r.get('payout') or 0,
            'settled_at': (r.get('settled_at') or '')[:16],
            'reason': reason,
            'is_win': is_win,
            'is_loss': is_loss,
        })
    return enriched

def create_workbook(all_bets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Remove default sheet

    # Group by sport
    by_sport = defaultdict(list)
    for b in all_bets:
        by_sport[b['sport']].append(b)

    # 1. Summary sheet
    create_summary_sheet(wb, all_bets, by_sport)

    # 2. Per-sport sheets
    sport_order = ["電競", "籃球", "棒球", "格鬥", "賽馬", "賽車", "卡牌對戰"]
    for sport in sport_order:
        if sport in by_sport:
            create_sport_sheet(wb, sport, by_sport[sport])

    # 3. Per-persona sheets
    by_persona = defaultdict(list)
    for b in all_bets:
        by_persona[b['persona']].append(b)

    create_all_bets_sheet(wb, all_bets)
    create_persona_summary_sheet(wb, by_persona)

    return wb

def create_summary_sheet(wb, all_bets, by_sport):
    ws = wb.create_sheet("📊 總覽")
    ws.sheet_view.showGridLines = False

    # Title
    ws.merge_cells('A1:J1')
    title_cell = ws['A1']
    title_cell.value = "🦞 龍蝦投注完整報告 — 分類統計總覽"
    title_cell.fill = PatternFill(fill_type="solid", fgColor="0D1117")
    title_cell.font = Font(bold=True, color="58A6FF", size=16)
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 40

    ws.merge_cells('A2:J2')
    sub = ws['A2']
    sub.value = f"生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  總投注筆數：{len(all_bets)}"
    sub.fill = PatternFill(fill_type="solid", fgColor="161B22")
    sub.font = Font(color="8B949E", size=10)
    sub.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 22

    # Category summary table
    ws.row_dimensions[3].height = 8

    headers = ["類別", "站台", "總注數", "勝利", "敗北", "無效", "勝率", "總投入(Pit)", "總獲利(Pit)", "淨損益(Pit)"]
    col_widths = [10, 20, 10, 8, 8, 8, 10, 15, 15, 15]

    for i, (h, w) in enumerate(zip(headers, col_widths)):
        col = get_column_letter(i+1)
        ws.column_dimensions[col].width = w
        cell = ws.cell(row=4, column=i+1, value=h)
        style_header_cell(cell, bg_color="21262D", fg_color="C9D1D9")

    ws.row_dimensions[4].height = 28

    sport_order = ["電競", "籃球", "棒球", "格鬥", "賽馬", "賽車", "卡牌對戰"]
    row = 5
    totals = {'total': 0, 'wins': 0, 'losses': 0, 'voids': 0, 'stake': 0, 'payout': 0}

    for sport in sport_order:
        bets = by_sport.get(sport, [])
        if not bets:
            continue

        wins = sum(1 for b in bets if b['is_win'])
        losses = sum(1 for b in bets if b['is_loss'])
        voids = len(bets) - wins - losses
        win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        total_stake = sum(b['stake'] for b in bets)
        total_payout = sum(b['payout'] for b in bets)
        net = total_payout - total_stake

        # Find site
        site = next((b['site'] for b in bets), '')

        color = CAT_COLORS.get(sport, "333333")

        row_data = [sport, site, len(bets), wins, losses, voids,
                   f"{win_rate:.1f}%", total_stake, total_payout, net]

        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col_idx, value=val)
            cell.fill = PatternFill(fill_type="solid", fgColor=color)
            cell.font = Font(color="FFFFFF", bold=(col_idx == 1), size=10)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border_thin()
            if col_idx == 7:  # win rate
                if win_rate >= 60:
                    cell.font = Font(color="69F0AE", bold=True, size=11)
                elif win_rate < 40:
                    cell.font = Font(color="FF5252", bold=True, size=11)
            if col_idx == 10:  # net
                if net > 0:
                    cell.font = Font(color="69F0AE", bold=True, size=10)
                elif net < 0:
                    cell.font = Font(color="FF5252", bold=True, size=10)

        ws.row_dimensions[row].height = 24

        totals['total'] += len(bets)
        totals['wins'] += wins
        totals['losses'] += losses
        totals['voids'] += voids
        totals['stake'] += total_stake
        totals['payout'] += total_payout
        row += 1

    # Total row
    total_wr = totals['wins'] / (totals['wins'] + totals['losses']) * 100 if (totals['wins'] + totals['losses']) > 0 else 0
    total_net = totals['payout'] - totals['stake']
    total_row = ["合計", "—", totals['total'], totals['wins'], totals['losses'], totals['voids'],
                f"{total_wr:.1f}%", totals['stake'], totals['payout'], total_net]

    for col_idx, val in enumerate(total_row, 1):
        cell = ws.cell(row=row, column=col_idx, value=val)
        cell.fill = PatternFill(fill_type="solid", fgColor="30363D")
        cell.font = Font(color="E6EDF3", bold=True, size=10)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border_thin()
    ws.row_dimensions[row].height = 26

    # Freeze rows
    ws.freeze_panes = 'A5'

def create_sport_sheet(wb, sport, bets):
    safe_name = sport[:31]
    ws = wb.create_sheet(f"🏟 {safe_name}")
    ws.sheet_view.showGridLines = False

    color = CAT_COLORS.get(sport, "333333")

    # Header
    ws.merge_cells('A1:K1')
    title = ws['A1']
    title.value = f"{sport} — 投注明細 ({len(bets)} 筆)"
    title.fill = PatternFill(fill_type="solid", fgColor=color)
    title.font = Font(bold=True, color="FFFFFF", size=14)
    title.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 36

    # Stats row
    wins = sum(1 for b in bets if b['is_win'])
    losses = sum(1 for b in bets if b['is_loss'])
    voids = len(bets) - wins - losses
    wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    total_stake = sum(b['stake'] for b in bets)
    total_payout = sum(b['payout'] for b in bets)
    net = total_payout - total_stake

    ws.merge_cells('A2:K2')
    stats = ws['A2']
    stats.value = (f"勝：{wins}  |  敗：{losses}  |  無效：{voids}  |  "
                   f"勝率：{wr:.1f}%  |  總投入：{total_stake:,} Pit  |  "
                   f"總獲利：{total_payout:,} Pit  |  淨損益：{'+' if net >= 0 else ''}{net:,} Pit")
    stats.fill = PatternFill(fill_type="solid", fgColor="0D1117")
    stats.font = Font(color="C9D1D9", size=10)
    stats.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 22

    # Column headers
    headers = ["#", "龍蝦", "賽事", "投注選項", "注碼(Pit)", "賠率", "信心", "結果", "賠付(Pit)", "時間", "理由"]
    col_widths = [5, 16, 30, 20, 12, 8, 10, 8, 12, 17, 60]

    for i, (h, w) in enumerate(zip(headers, col_widths)):
        col = get_column_letter(i+1)
        ws.column_dimensions[col].width = w
        cell = ws.cell(row=3, column=i+1, value=h)
        style_header_cell(cell, bg_color="21262D", fg_color="C9D1D9")
    ws.row_dimensions[3].height = 26

    # Data rows
    # Sort by persona, then time
    sorted_bets = sorted(bets, key=lambda x: (x['persona'], x['settled_at']))

    for idx, bet in enumerate(sorted_bets, 1):
        row = idx + 3
        outcome = bet['outcome']
        if outcome == 'win':
            row_bg = "162620"
        elif outcome == 'loss':
            row_bg = "2A1515"
        else:
            row_bg = "1C1C1C"

        row_data = [
            idx,
            PERSONA_DISPLAY.get(bet['persona'], bet['persona']),
            bet['event_title'],
            bet['pick'],
            bet['stake'],
            bet['odds'],
            bet['confidence'],
            '✓ 勝' if outcome == 'win' else ('✗ 敗' if outcome == 'loss' else '— 無效'),
            bet['payout'],
            bet['settled_at'],
            bet['reason'],
        ]

        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col_idx, value=val)
            cell.fill = PatternFill(fill_type="solid", fgColor=row_bg)

            fg = "E6EDF3"
            if col_idx == 8:  # outcome
                if outcome == 'win':
                    fg = "69F0AE"
                elif outcome == 'loss':
                    fg = "FF5252"
                else:
                    fg = "9E9E9E"
            elif col_idx == 9:  # payout
                fg = "69F0AE" if bet['payout'] > bet['stake'] else "FF5252" if bet['payout'] == 0 and outcome == 'loss' else "9E9E9E"

            cell.font = Font(color=fg, size=9, bold=(col_idx == 8))
            cell.alignment = Alignment(
                horizontal='left' if col_idx in (3, 4, 11) else 'center',
                vertical='top' if col_idx == 11 else 'center',
                wrap_text=(col_idx == 11)
            )
            cell.border = border_thin()

        # Adjust row height for reason
        reason_len = len(bet['reason'])
        ws.row_dimensions[row].height = max(20, min(80, reason_len // 5 * 15))

    ws.freeze_panes = 'A4'

def create_all_bets_sheet(wb, all_bets):
    ws = wb.create_sheet("📋 全部投注")
    ws.sheet_view.showGridLines = False

    ws.merge_cells('A1:L1')
    title = ws['A1']
    title.value = f"全部龍蝦投注明細 — {len(all_bets)} 筆"
    title.fill = PatternFill(fill_type="solid", fgColor="0D1117")
    title.font = Font(bold=True, color="58A6FF", size=14)
    title.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 36

    headers = ["#", "龍蝦", "類別", "站台", "賽事", "投注選項", "注碼(Pit)", "賠率", "信心", "結果", "賠付(Pit)", "時間"]
    col_widths = [5, 16, 8, 15, 30, 20, 12, 8, 10, 8, 12, 17]

    for i, (h, w) in enumerate(zip(headers, col_widths)):
        col = get_column_letter(i+1)
        ws.column_dimensions[col].width = w
        cell = ws.cell(row=2, column=i+1, value=h)
        style_header_cell(cell, bg_color="21262D", fg_color="C9D1D9")
    ws.row_dimensions[2].height = 26

    sorted_all = sorted(all_bets, key=lambda x: x['settled_at'])

    for idx, bet in enumerate(sorted_all, 1):
        row = idx + 2
        outcome = bet['outcome']
        row_bg = "162620" if outcome == 'win' else ("2A1515" if outcome == 'loss' else "1C1C1C")

        sport = bet['sport']
        sport_color = CAT_COLORS.get(sport, "555555")

        row_data = [
            idx,
            PERSONA_DISPLAY.get(bet['persona'], bet['persona']),
            sport,
            bet['site'],
            bet['event_title'],
            bet['pick'],
            bet['stake'],
            bet['odds'],
            bet['confidence'],
            '✓ 勝' if outcome == 'win' else ('✗ 敗' if outcome == 'loss' else '— 無效'),
            bet['payout'],
            bet['settled_at'],
        ]

        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col_idx, value=val)
            cell.fill = PatternFill(fill_type="solid", fgColor=row_bg)
            fg = "E6EDF3"
            if col_idx == 3:
                cell.fill = PatternFill(fill_type="solid", fgColor=sport_color)
                fg = "FFFFFF"
            elif col_idx == 10:
                fg = "69F0AE" if outcome == 'win' else ("FF5252" if outcome == 'loss' else "9E9E9E")
            cell.font = Font(color=fg, size=9, bold=(col_idx == 10))
            cell.alignment = Alignment(horizontal='center' if col_idx not in (2,5,6) else 'left', vertical='center')
            cell.border = border_thin()

        ws.row_dimensions[row].height = 18

    ws.freeze_panes = 'A3'

def create_persona_summary_sheet(wb, by_persona):
    ws = wb.create_sheet("🦞 龍蝦個人統計")
    ws.sheet_view.showGridLines = False

    ws.merge_cells('A1:N1')
    title = ws['A1']
    title.value = "龍蝦個人統計 — 含各類別細分"
    title.fill = PatternFill(fill_type="solid", fgColor="0D1117")
    title.font = Font(bold=True, color="58A6FF", size=14)
    title.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 36

    sport_order = ["電競", "籃球", "棒球", "格鬥", "賽馬", "賽車", "卡牌對戰"]

    # Main headers
    headers = ["龍蝦", "總注數", "總勝", "總敗", "總勝率", "總投入", "總獲利", "淨損益"] + sport_order
    col_widths = [18, 8, 7, 7, 10, 12, 12, 12] + [12] * len(sport_order)

    for i, (h, w) in enumerate(zip(headers, col_widths)):
        col = get_column_letter(i+1)
        ws.column_dimensions[col].width = w
        cell = ws.cell(row=2, column=i+1, value=h)
        style_header_cell(cell, bg_color="21262D", fg_color="C9D1D9")
    ws.row_dimensions[2].height = 28

    # Sub-headers for sport columns
    for si, sport in enumerate(sport_order):
        col = get_column_letter(9 + si)
        color = CAT_COLORS.get(sport, "333333")
        cell = ws.cell(row=2, column=9 + si)
        cell.fill = PatternFill(fill_type="solid", fgColor=color)

    persona_order = [
        'norika_oda', 'clawtrix', 'pincerbot', 'ragclaw', 'snaplex',
        'hexclaw', 'vortexiq', 'prismbit', 'stonefang', 'blazepaw',
        'basaltrook', 'brineledger', 'emberling', 'flarecub', 'gauntlet',
        'hexclaw', 'mirrorcalm', 'nullpinch', 'omenwake', 'rushfin', 'sparkrill'
    ]
    seen = set()
    ordered_personas = []
    for p in persona_order:
        if p not in seen and p in by_persona:
            ordered_personas.append(p)
            seen.add(p)
    for p in by_persona:
        if p not in seen:
            ordered_personas.append(p)
            seen.add(p)

    is_active = {'norika_oda', 'clawtrix', 'pincerbot', 'ragclaw', 'snaplex',
                 'hexclaw', 'vortexiq', 'prismbit', 'stonefang', 'blazepaw'}

    row = 3
    for persona in ordered_personas:
        bets = by_persona[persona]
        wins = sum(1 for b in bets if b['is_win'])
        losses = sum(1 for b in bets if b['is_loss'])
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        total_stake = sum(b['stake'] for b in bets)
        total_payout = sum(b['payout'] for b in bets)
        net = total_payout - total_stake

        # Per-sport
        sport_stats = {}
        by_s = defaultdict(list)
        for b in bets:
            by_s[b['sport']].append(b)
        for sport in sport_order:
            sb = by_s.get(sport, [])
            sw = sum(1 for b in sb if b['is_win'])
            sl = sum(1 for b in sb if b['is_loss'])
            swr = sw / (sw + sl) * 100 if (sw + sl) > 0 else 0
            sport_stats[sport] = f"{len(sb)}注\n{sw}勝/{sl}敗\n{swr:.0f}%" if sb else "—"

        is_act = persona in is_active
        row_bg = "161B22" if is_act else "0D1117"

        row_data = [
            PERSONA_DISPLAY.get(persona, persona),
            len(bets), wins, losses,
            f"{wr:.1f}%",
            total_stake, total_payout, net
        ] + [sport_stats[s] for s in sport_order]

        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col_idx, value=val)
            cell.fill = PatternFill(fill_type="solid", fgColor=row_bg)

            fg = "C9D1D9" if is_act else "8B949E"
            bold = False
            if col_idx == 1:
                fg = "E6EDF3" if is_act else "8B949E"
                bold = is_act
            elif col_idx == 5:
                if wr >= 60:
                    fg = "69F0AE"
                    bold = True
                elif wr < 40:
                    fg = "FF5252"
                    bold = True
            elif col_idx == 8:
                fg = "69F0AE" if net > 0 else "FF5252" if net < 0 else "9E9E9E"
                bold = True
            elif col_idx >= 9:
                # Sport column
                sport = sport_order[col_idx - 9]
                sport_color = CAT_COLORS.get(sport, "333333")
                if val != "—":
                    cell.fill = PatternFill(fill_type="solid", fgColor=sport_color)
                    fg = "FFFFFF"
                else:
                    cell.fill = PatternFill(fill_type="solid", fgColor="0D1117")
                    fg = "30363D"

            cell.font = Font(color=fg, size=9, bold=bold)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = border_thin()

        ws.row_dimensions[row].height = 52 if any(sport_stats[s] != "—" for s in sport_order) else 20
        row += 1

    ws.freeze_panes = 'A3'

def main():
    print("📦 載入資料庫投注記錄...")
    db_bets = load_all_bets()
    print(f"   ✓ 資料庫：{len(db_bets)} 筆")

    print("📦 載入卡牌對戰記憶...")
    card_bets = load_card_bets()
    print(f"   ✓ 卡牌：{len(card_bets)} 筆")

    all_raw = db_bets + card_bets
    print(f"   ✓ 總計：{len(all_raw)} 筆")

    print("🔄 整理資料...")
    all_bets = enrich_bets(all_raw)

    # Stats
    by_sport = defaultdict(list)
    for b in all_bets:
        by_sport[b['sport']].append(b)
    print("\n📊 各類別統計：")
    for sport, bets in sorted(by_sport.items()):
        wins = sum(1 for b in bets if b['is_win'])
        losses = sum(1 for b in bets if b['is_loss'])
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        print(f"   {sport}: {len(bets)} 注 | {wins}勝 {losses}敗 | 勝率 {wr:.1f}%")

    print("\n🎨 生成 Excel...")
    wb = create_workbook(all_bets)
    wb.save(OUTPUT_PATH)
    print(f"\n✅ 完成！檔案已儲存至：{OUTPUT_PATH}")

    import os
    size = os.path.getsize(OUTPUT_PATH)
    print(f"   檔案大小：{size/1024:.1f} KB")

if __name__ == '__main__':
    main()
