#!/usr/bin/env python3
"""
Charenix 全面性單元測試
覆蓋範圍：
  - [REGRESSION] Lounge 不重複發文（修復 2026-04-30）
  - battle_engine：match 建立、出牌、Pass、換牌、AI 決策、勝負判定
  - skill_engine：role 分配、build_effects、level scaling
  - combo：組合技偵測
  - raid_engine：Raid 建立、狀態、AI 自動出牌
  - Live API：battle_arena(8004) + raid_boss(8015) health check
  - Lounge API：無重複訊息存入
"""
import sys, os, ast, re
sys.path.insert(0, "/opt/battle_arena/app")
sys.path.insert(0, "/opt/raid_boss/app")

import pytest
import battle_engine as be
import skill_engine as se
import combo as co

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: REGRESSION — lounge 不重複發文
# ─────────────────────────────────────────────────────────────────────────────

class TestLoungeNoDuplicate:
    """確保 _lob_post 只往單一 channel 發一次，不 mirror 到其他頻道。"""

    def test_lob_post_no_mirror_logic(self):
        raid_boss_main = "/opt/raid_boss/app/main.py"
        src = open(raid_boss_main).read()
        # 不能有雙頻道 mirror：targets.append("生活閒聊")
        assert 'targets.append("生活閒聊")' not in src, (
            "⛔ _lob_post 再度出現雙頻道 mirror！每條訊息只能發一次！"
        )
        assert 'targets.append' not in src, (
            "⛔ _lob_post 裡出現 targets.append，可能是新的雙頻道邏輯！"
        )

    def test_lob_post_single_http_call(self):
        """_lob_post 函數只能有一個 await c.post("""
        src = open("/opt/raid_boss/app/main.py").read()
        # 找 _lob_post 函數範圍
        start = src.find("async def _lob_post(")
        end   = src.find("\nasync def ", start + 1)
        func_src = src[start:end]
        post_count = func_src.count("await c.post(")
        assert post_count == 1, (
            f"⛔ _lob_post 裡有 {post_count} 個 await c.post！只能有 1 個！"
        )

    def test_frontend_dedup_present(self):
        """battle_arena lounge HTML 必須包含 _seenMsgKeys dedup 邏輯。"""
        src = open("/opt/battle_arena/app/main.py").read()
        assert "_seenMsgKeys" in src, "⛔ 前端 dedup Set 不見了！"
        assert "_seenMsgKeys.has(key)" in src, "⛔ dedup 的 has() 檢查不見了！"
        assert "_seenMsgKeys.add(key)" in src, "⛔ dedup 的 add() 不見了！"

    def test_lounge_msg_endpoint_saves_once(self):
        """battle_arena lounge/msg endpoint：一次 POST 只存一次、broadcast 一次。"""
        src = open("/opt/battle_arena/app/main.py").read()
        # 找 lounge_msg 函數
        start = src.find('async def lounge_msg(')
        end   = src.find('\n@app.', start + 1)
        func_src = src[start:end]
        assert func_src.count("_lounge_save(") == 1, "⛔ _lounge_save 被呼叫多次！"
        assert func_src.count("_lounge_broadcast(") == 1, "⛔ _lounge_broadcast 被呼叫多次！"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: battle_engine — 核心戰鬥邏輯
# ─────────────────────────────────────────────────────────────────────────────

class TestBattleEngineInit:
    def test_start_match_creates_match(self):
        m = be.start_match("alice", "bob", bet=1000, seed=42)
        assert m.player_a == "alice"
        assert m.player_b == "bob"
        assert m.bet == 1000
        assert m.hp["a"] == be.INITIAL_HP
        assert m.hp["b"] == be.INITIAL_HP
        assert m.mp["a"] == be.INITIAL_MP
        assert m.mp["b"] == be.INITIAL_MP
        assert m.round == 1
        assert m.winner is None

    def test_start_match_hands_dealt(self):
        m = be.start_match(seed=1)
        assert len(m.hand["a"]) == be.HAND_SIZE
        assert len(m.hand["b"]) == be.HAND_SIZE

    def test_hand_attacker_minimum(self):
        """手牌初始至少 3 張 attacker/dual。"""
        for seed in range(20):
            m = be.start_match(seed=seed)
            for side in ("a", "b"):
                att = m._count_hand_role(side, ("attacker", "dual"))
                assert att >= 3, f"seed={seed} side={side} attackers={att} < 3"

    def test_get_match_returns_same(self):
        m = be.start_match(seed=0)
        m2 = be.get_match(m.id)
        assert m is m2

    def test_get_match_missing_raises(self):
        with pytest.raises(be.MatchError):
            be.get_match("nonexistent-id")

    def test_match_has_weather(self):
        m = be.start_match(seed=5)
        assert "id" in m.weather
        assert "cn" in m.weather


class TestBattleEnginePassTurn:
    def test_both_pass_resolves_round(self):
        m = be.start_match(seed=99)
        be.pass_turn(m.id, "a")
        res_b = be.pass_turn(m.id, "b")
        # after both pass, current_round advances to 2 (or match ends)
        assert res_b.get("current_round") == 2 or m.winner is not None

    def test_double_pass_raises(self):
        m = be.start_match(seed=7)
        be.pass_turn(m.id, "a")
        with pytest.raises(be.MatchError, match="已經出牌"):
            be.pass_turn(m.id, "a")

    def test_pass_invalid_side_raises(self):
        m = be.start_match(seed=3)
        with pytest.raises(be.MatchError):
            be.pass_turn(m.id, "c")

    def test_pass_ended_match_raises(self):
        m = be.start_match(seed=11)
        m.winner = "a"
        with pytest.raises(be.MatchError):
            be.pass_turn(m.id, "b")


class TestBattleEnginePlayTurn:
    def _attacker_id(self, m, side):
        cards = m.hand_as_cards(side)
        att = [c for c in cards if c.get("role") in ("attacker", "dual")]
        return att[0]["id"] if att else cards[0]["id"]

    def test_play_attacker_resolves(self):
        m = be.start_match(seed=42)
        att_a = self._attacker_id(m, "a")
        att_b = self._attacker_id(m, "b")
        be.play_turn(m.id, "a", att_a, None, None, 0)
        res = be.play_turn(m.id, "b", att_b, None, None, 0)
        assert isinstance(res, dict)

    def test_double_play_raises(self):
        m = be.start_match(seed=13)
        att = self._attacker_id(m, "a")
        be.play_turn(m.id, "a", att, None, None, 0)
        with pytest.raises(be.MatchError, match="已經出牌"):
            be.play_turn(m.id, "a", att, None, None, 0)

    def test_hp_decreases_after_combat(self):
        """打一回合後至少一方 HP 應下降。"""
        m = be.start_match(seed=77)
        att_a = self._attacker_id(m, "a")
        att_b = self._attacker_id(m, "b")
        be.play_turn(m.id, "a", att_a, None, None, 0)
        be.play_turn(m.id, "b", att_b, None, None, 0)
        hp_sum = m.hp["a"] + m.hp["b"]
        assert hp_sum < be.INITIAL_HP * 2, "打了一回合但 HP 沒變！"

    def test_card_not_in_hand_raises(self):
        m = be.start_match(seed=5)
        with pytest.raises(be.MatchError):
            be.play_turn(m.id, "a", "fake-card-id-999", None, None, 0)


class TestBattleEngineSwap:
    def test_swap_updates_hand(self):
        m = be.start_match(seed=1)
        old_hand = list(m.hand["a"])
        swap_ids = old_hand[:2]
        res = be.swap_cards(m.id, "a", swap_ids)
        assert "drawn" in res
        assert len(res["drawn"]) == 2
        for cid in swap_ids:
            assert cid not in m.hand["a"]

    def test_swap_twice_same_round_raises(self):
        m = be.start_match(seed=2)
        swap_ids = m.hand["a"][:1]
        be.swap_cards(m.id, "a", swap_ids)
        with pytest.raises(be.MatchError, match="已用過換牌"):
            be.swap_cards(m.id, "a", m.hand["a"][:1])

    def test_swap_too_many_raises(self):
        m = be.start_match(seed=3)
        with pytest.raises(be.MatchError, match="最多換"):
            be.swap_cards(m.id, "a", m.hand["a"][:4])

    def test_swap_maintains_attacker_min(self):
        """換牌後 attacker 不低於 3 張。"""
        for seed in range(10):
            m = be.start_match(seed=seed)
            be.swap_cards(m.id, "a", m.hand["a"][:3])
            att = m._count_hand_role("a", ("attacker", "dual"))
            assert att >= 3, f"seed={seed} 換牌後 attacker={att} < 3"


class TestBattleEngineFullGame:
    def test_ai_vs_ai_completes(self):
        """AI vs AI 必定在 20 回合內結束，不會卡死。"""
        m = be.start_match("AI_A", "AI_B", bet=500, seed=10)
        while not m.winner and m.round <= be.MAX_ROUNDS + 2:
            for side in ("a", "b"):
                if m.pending[side] is not None:
                    continue
                pick = be.ai_pick_move(m, side)
                if pick["action"] == "pass":
                    be.pass_turn(m.id, side)
                else:
                    be.play_turn(m.id, side,
                                 pick.get("attacker"), pick.get("buffer"),
                                 pick.get("debuffer"), pick.get("raise_amount", 0))
        assert m.winner is not None, "比賽沒有結束！"
        assert m.round <= be.MAX_ROUNDS + 1

    def test_ai_vs_ai_winner_valid(self):
        m = be.start_match(seed=20)
        while not m.winner:
            for side in ("a", "b"):
                if m.pending[side] is not None:
                    continue
                pick = be.ai_pick_move(m, side)
                if pick["action"] == "pass":
                    be.pass_turn(m.id, side)
                else:
                    be.play_turn(m.id, side,
                                 pick.get("attacker"), pick.get("buffer"),
                                 pick.get("debuffer"), pick.get("raise_amount", 0))
        # winner can be "a"/"A"/"b"/"B" or draw variant
        assert m.winner.lower() in ("a", "b", "draw")

    def test_multiple_matches_independent(self):
        """多場同時進行不互相干擾。"""
        m1 = be.start_match(seed=1)
        m2 = be.start_match(seed=2)
        assert m1.id != m2.id
        be.pass_turn(m1.id, "a")
        # m2 不受影響
        assert m2.pending["a"] is None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: skill_engine — role 分配與技能縮放
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillEngine:
    def _make_card(self, ovr, element, buff=None, debuff=None):
        return {
            "id": "test", "ovr": ovr, "element": element,
            "level": 20, "sport": "F1",
            "buff_effect": buff or {},
            "debuff_effect": debuff or {},
        }

    def test_fire_high_ovr_attacker(self):
        card = self._make_card(80, "fire")
        assert se.assign_role(card) == "attacker"

    def test_water_with_buff_supporter(self):
        card = self._make_card(72, "water", buff={"type": "self_atk_pct", "value": 10})
        assert se.assign_role(card) == "supporter"

    def test_earth_high_ovr_debuff_disruptor(self):
        card = self._make_card(82, "earth", debuff={"type": "opp_atk_flat", "value": 15})
        assert se.assign_role(card) == "disruptor"

    def test_legendary_dual_both_effects(self):
        card = self._make_card(
            87, "fire",
            buff={"type": "self_atk_pct", "value": 15},
            debuff={"type": "opp_atk_flat", "value": 10},
        )
        assert se.assign_role(card) == "dual"

    def test_level_scaling_atk_increases(self):
        atk_l1  = se.atk_at_level(80, 1)
        atk_l50 = se.atk_at_level(80, 50)
        assert atk_l50 > atk_l1, "高等級 ATK 應高於低等級"

    def test_level_scaling_def_increases(self):
        def_l1  = se.def_at_level(80, 1)
        def_l50 = se.def_at_level(80, 50)
        assert def_l50 > def_l1, "高等級 DEF 應高於低等級"

    def test_mp_at_level_positive(self):
        mp = se.mp_at_level(60, 20)
        assert mp > 0

    def test_all_roles_valid(self):
        valid = {"attacker", "supporter", "disruptor", "dual"}
        elements = ["fire", "water", "wind", "earth"]
        for elem in elements:
            for ovr in [65, 75, 85, 95]:
                card = self._make_card(ovr, elem,
                    buff={"type": "self_atk_pct", "value": 10},
                    debuff={"type": "opp_atk_flat", "value": 8})
                role = se.assign_role(card)
                assert role in valid, f"elem={elem} ovr={ovr} → 無效 role={role}"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: combo — 組合技偵測
# ─────────────────────────────────────────────────────────────────────────────

class TestCombo:
    def _card(self, ovr, element, sport, rarity="Normal"):
        return {"ovr": ovr, "element": element, "sport": sport, "rarity": rarity}

    def test_weak_alliance_all_low_ovr(self):
        deck = [self._card(70, "fire", "F1"),
                self._card(68, "water", "Horse"),
                self._card(75, "wind", "UFC")]
        flags = co.detect_combos(deck)
        assert flags["weak_alliance"] is True

    def test_weak_alliance_false_if_high_ovr(self):
        deck = [self._card(80, "fire", "F1"),
                self._card(70, "water", "Horse"),
                self._card(75, "wind", "UFC")]
        flags = co.detect_combos(deck)
        assert flags["weak_alliance"] is False

    def test_rain_tactic_two_water(self):
        deck = [self._card(70, "water", "F1"),
                self._card(72, "water", "Horse"),
                self._card(75, "fire", "UFC")]
        flags = co.detect_combos(deck)
        assert flags["rain_tactic"] is True

    def test_rain_tactic_false_one_water(self):
        deck = [self._card(70, "water", "F1"),
                self._card(72, "fire", "Horse"),
                self._card(75, "fire", "UFC")]
        flags = co.detect_combos(deck)
        assert flags["rain_tactic"] is False

    def test_speed_league_f1_plus_horse(self):
        deck = [self._card(70, "fire", "F1"),
                self._card(72, "wind", "Horse"),
                self._card(75, "earth", "UFC")]
        flags = co.detect_combos(deck)
        assert flags["speed_league"] is True

    def test_purebred_same_sport(self):
        deck = [self._card(70, "fire", "F1"),
                self._card(72, "water", "F1"),
                self._card(75, "wind", "F1")]
        flags = co.detect_combos(deck)
        assert flags["purebred"] is True

    def test_purebred_false_mixed_sports(self):
        deck = [self._card(70, "fire", "F1"),
                self._card(72, "water", "Horse"),
                self._card(75, "wind", "F1")]
        flags = co.detect_combos(deck)
        assert flags["purebred"] is False

    def test_crossover_legend_three_sports(self):
        deck = [self._card(90, "fire", "F1", "Legend"),
                self._card(88, "water", "Horse", "Legend"),
                self._card(92, "wind", "UFC", "Legend")]
        flags = co.detect_combos(deck)
        assert flags["crossover_legend"] is True

    def test_no_combo_plain_deck(self):
        # 高 OVR + 單一 water + 不同運動 (非F1+Horse) + 非 Legend → 無任何組合技
        deck = [self._card(80, "fire", "UFC"),
                self._card(78, "wind", "NBA"),
                self._card(82, "earth", "Esports")]
        flags = co.detect_combos(deck)
        assert not any(flags.values()), f"普通牌組不應觸發任何組合技，但觸發了：{[k for k,v in flags.items() if v]}"

    def test_combo_label_list_format(self):
        deck = [self._card(70, "water", "F1"),
                self._card(72, "water", "Horse"),
                self._card(75, "fire", "UFC")]
        flags = co.detect_combos(deck)
        labels = co.combo_label_list(flags)
        for lbl in labels:
            assert "key" in lbl and "cn" in lbl and "icon" in lbl


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: raid_engine — Raid 基本邏輯
# ─────────────────────────────────────────────────────────────────────────────

class TestRaidEngine:
    def setup_method(self):
        import raid_engine as re_
        self.re = re_

    def test_create_raid_single_player(self):
        r = self.re.create_raid(["snaplex"], seed=1)
        assert r.boss_hp > 0
        assert r.hp["snaplex"] > 0
        assert r.round == 1
        assert r.winner is None

    def test_create_raid_three_players(self):
        r = self.re.create_raid(["snaplex", "ragclaw", "hexclaw"], seed=2)
        assert len(r.raiders) == 3
        for name in r.raiders:
            assert name in r.hp
            assert len(r.hand[name]) > 0

    def test_raid_boss_hp_scales_with_players(self):
        r1 = self.re.create_raid(["p1"], seed=5)
        r3 = self.re.create_raid(["p1", "p2", "p3"], seed=5)
        assert r3.boss_max_hp > r1.boss_max_hp

    def test_get_raid_returns_same(self):
        r = self.re.create_raid(["hexclaw"], seed=7)
        r2 = self.re.get_raid(r.id)
        assert r is r2

    def test_get_raid_missing_returns_none(self):
        r = self.re.get_raid("nonexistent-raid-id")
        assert r is None

    def test_raid_public_state_keys(self):
        r = self.re.create_raid(["snaplex"], seed=3)
        state = r.public_state("snaplex")
        assert "boss" in state
        assert "hp" in state["boss"]
        assert "max_hp" in state["boss"]
        assert "round" in state
        assert "weather" in state
        assert "raiders_hp" in state

    def test_raid_hand_not_empty(self):
        r = self.re.create_raid(["norika_oda"], seed=9)
        assert len(r.hand["norika_oda"]) >= 4

    def test_raid_ai_auto_completes(self):
        """純 AI Raid（ai_auto_submit 自動 resolve）不卡死。"""
        r = self.re.create_raid(["alice", "bob"], seed=42, human_names=[])
        rounds = 0
        while not r.winner and rounds < 80:
            # ai_auto_submit with empty human list resolves automatically
            r.ai_auto_submit([])
            rounds += 1
        assert r.winner is not None, f"Raid {rounds} 回合後仍無勝負"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: Live API health checks
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveAPI:
    @pytest.fixture(autouse=True)
    def _http(self):
        import httpx
        self.c = httpx.Client(base_url="http://localhost:8004", timeout=5)
        self.raid = httpx.Client(base_url="http://localhost:8015", timeout=5)
        yield
        self.c.close()
        self.raid.close()

    def test_battle_arena_lounge_channels(self):
        r = self.c.get("/lounge/channels")
        assert r.status_code == 200
        data = r.json()
        assert "channels" in data
        assert "卡牌對戰" in data["channels"]
        assert "生活閒聊" in data["channels"]

    def test_battle_arena_lounge_history(self):
        r = self.c.get("/lounge/history")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_battle_arena_lounge_history_no_duplicate_in_db(self):
        """同一時間戳+同一 name 的訊息不應在 DB 裡出現超過 1 次。"""
        r = self.c.get("/lounge/history")
        msgs = r.json()
        seen = {}
        for m in msgs:
            key = (m.get("name",""), m.get("time",""), m.get("text","")[:40])
            assert key not in seen, f"⛔ Lounge 歷史有重複訊息！key={key}"
            seen[key] = True

    def test_battle_arena_room_list(self):
        r = self.c.get("/api/room/list")
        assert r.status_code == 200

    def test_battle_arena_cards_endpoint(self):
        r = self.c.get("/api/cards")
        assert r.status_code == 200
        cards = r.json()
        assert len(cards) > 0

    def test_raid_boss_health(self):
        r = self.raid.get("/api/raid/boss")
        assert r.status_code == 200
        data = r.json()
        assert "boss_hp" in data or "era" in data

    def test_raid_boss_leaderboard(self):
        r = self.raid.get("/api/raid/leaderboard")
        assert r.status_code in (200, 404)  # ok if not implemented

    def test_battle_arena_lounge_page_has_dedup(self):
        """確認上線中的 HTML 包含 dedup 邏輯。"""
        r = self.c.get("/lounge")
        assert r.status_code == 200
        assert "_seenMsgKeys" in r.text, "⛔ 上線的 lounge 頁面沒有 dedup！"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: Lounge 防重複發文 E2E
# ─────────────────────────────────────────────────────────────────────────────

class TestLoungeNoRepeatE2E:
    """模擬龍蝦發文，確認同一則訊息不會出現兩次。"""

    SVC_KEY = "60227b36cfa322b7b61d2ee116cba5c2899c2aef704637ae479f7fcda2e4f29e"

    @pytest.fixture(autouse=True)
    def _setup(self):
        import httpx
        self.c = httpx.Client(base_url="http://localhost:8004", timeout=5)
        r = self.c.post("/lounge/join", json={
            "name": "test_lobster_unit",
            "password": self.SVC_KEY,
        })
        assert r.status_code == 200, f"lounge/join failed: {r.text}"
        self.token = r.json().get("lobster_token", "")
        yield
        self.c.close()

    def test_single_post_appears_once(self):
        import time, uuid
        unique_text = f"UNIT_TEST_{uuid.uuid4().hex[:8]}"
        hdrs = {"X-Lobster-Key": self.token}
        self.c.post("/lounge/msg", json={
            "name": "test_lobster_unit",
            "text": unique_text,
            "channel": "卡牌對戰",
        }, headers=hdrs)
        time.sleep(0.2)
        msgs = self.c.get("/lounge/history").json()
        matching = [m for m in msgs if m.get("text","") == unique_text]
        assert len(matching) == 1, (
            f"⛔ 發了 1 次，但 lounge 出現 {len(matching)} 條相同訊息！"
        )

    def test_channel_not_mirrored(self):
        """發到「卡牌對戰」不應同時出現在「生活閒聊」。"""
        import time, uuid
        unique_text = f"MIRROR_TEST_{uuid.uuid4().hex[:8]}"
        hdrs = {"X-Lobster-Key": self.token}
        self.c.post("/lounge/msg", json={
            "name": "test_lobster_unit",
            "text": unique_text,
            "channel": "卡牌對戰",
        }, headers=hdrs)
        time.sleep(0.2)
        msgs = self.c.get("/lounge/history").json()
        channels = [m["channel"] for m in msgs if m.get("text","") == unique_text]
        assert len(channels) == 1, f"⛔ 訊息出現在 {len(channels)} 個頻道（應只有 1）"
        assert channels[0] == "卡牌對戰", f"⛔ channel 錯誤: {channels[0]}"
