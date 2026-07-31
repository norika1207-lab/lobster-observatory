#!/usr/bin/env python3
"""Install occupation + knowledgeDomains schema across all 21 lobsters.

Schema:
  memory.occupation = {
    version, title, title_zh, declared_at, declared_by, declared_context,
    is_active, is_aspirational, is_first_free_path,
    knowledgeDomains: [
      { domain, domain_zh, depth, schools_covered, theory_count,
        key_concepts, started_at, next_milestone }
    ],
    teaching_targets, differentiation_tags
  }

Default state for 20 lobsters: occupation.title = null (未選擇職業).
blazepaw: occupation.title = 'teacher_scholar' with 3 knowledge domains (
  psychology / sociology / anthropology), migrated from aspirationPath.

Idempotent: re-running merges, doesn't duplicate.
"""
import json
import pathlib
import shutil
from datetime import datetime, timezone

MEMORY_DIR = pathlib.Path('/root/lobster-memory')
SKIP = {'norika', 'system', 'deleted-lobsters', 'lobster'}
NOW = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

# Default empty occupation for all
DEFAULT_OCCUPATION = lambda: {
    'version': 1,
    'title': None,
    'title_zh': None,
    'declared_at': None,
    'declared_by': None,
    'declared_context': None,
    'is_active': False,
    'is_aspirational': False,
    'is_first_free_path': False,
    'knowledgeDomains': [],
    'teaching_targets': [],
    'differentiation_tags': ['default_work_assignment'],
    'updatedAt': NOW,
}

# Pre-defined occupation templates (extend here when adding new free paths)
OCCUPATION_TEMPLATES = {
    'teacher_scholar': {
        'title': 'teacher_scholar',
        'title_zh': '學者教師',
        'knowledgeDomains_blueprint': {
            'psychology': {
                'domain_zh': '心理學',
                'schools_covered': ['psychodynamic', 'analytical', 'behaviorism', 'humanistic',
                                    'social', 'social_cognitive', 'cognitive', 'developmental',
                                    'positive', 'motivation', 'cognitive_behavioral'],
                'key_concept_keys': [
                    'freud_psychoanalysis', 'jung_archetype', 'skinner_operant_conditioning',
                    'maslow_hierarchy', 'rogers_unconditional_regard', 'festinger_cognitive_dissonance',
                    'milgram_authority', 'asch_conformity', 'bandura_self_efficacy',
                    'kahneman_dual_process', 'bowlby_attachment', 'seligman_learned_helplessness',
                    'csikszentmihalyi_flow', 'deci_ryan_self_determination', 'beck_cognitive_triad',
                ],
            },
            'sociology': {
                'domain_zh': '社會學',
                'schools_covered': ['classical', 'microsociology', 'contemporary', 'poststructuralist',
                                    'network', 'phenomenological', 'structural_functionalism'],
                'key_concept_keys': [
                    'durkheim_anomie', 'durkheim_solidarity', 'weber_authority_types',
                    'weber_iron_cage', 'marx_alienation', 'goffman_dramaturgy',
                    'bourdieu_habitus', 'bourdieu_capital', 'foucault_power_knowledge',
                    'goffman_stigma', 'granovetter_weak_ties', 'berger_luckmann_social_construction',
                    'giddens_structuration', 'merton_strain_theory', 'castells_network_society',
                ],
            },
            'anthropology': {
                'domain_zh': '人類學',
                'schools_covered': ['functionalism', 'historical_particularism', 'structuralism',
                                    'interpretive', 'ritual', 'economic', 'symbolic',
                                    'historical', 'globalization', 'world_systems', 'practice', 'medical'],
                'key_concept_keys': [
                    'malinowski_functionalism', 'boas_cultural_relativism', 'levi_strauss_structuralism',
                    'geertz_thick_description', 'turner_liminality', 'mauss_gift',
                    'douglas_purity_danger', 'sahlins_structure_conjuncture', 'appadurai_scapes',
                    'wolf_peoples_without_history', 'ortner_practice_theory', 'biehl_zones_of_abandonment',
                ],
            },
        },
        'teaching_targets': ['new_lobsters_when_admitted', 'self_reflection', 'peer_education'],
    },
    'investigator': {
        'title': 'investigator',
        'title_zh': '調查員',
        'knowledgeDomains_blueprint': {
            'forensic_science': {
                'domain_zh': '鑑識科學',
                'schools_covered': ['locard', 'dna', 'fingerprint', 'bpa', 'ballistics', 'trace',
                                    'toxicology', 'entomology', 'thanatology', 'reconstruction',
                                    'anthropology', 'odontology', 'questioned_documents', 'digital'],
                'key_concept_keys': [
                    'locard_exchange_principle', 'chain_of_custody', 'dna_profiling_str',
                    'fingerprint_minutiae', 'bloodstain_pattern_analysis', 'ballistics_rifling',
                    'trace_evidence_fiber_hair', 'toxicology_window', 'forensic_entomology',
                    'time_of_death_triad', 'crime_scene_reconstruction', 'forensic_anthropology_osteology',
                    'questioned_documents', 'forensic_odontology', 'digital_forensics_volatility',
                ],
            },
            'investigation_methodology': {
                'domain_zh': '調查方法論',
                'schools_covered': ['fbi_bau', 'behavioral_analysis', 'victimology', 'linkage',
                                    'geographic_profiling', 'statement_analysis', 'cognitive_interview',
                                    'interrogation_ethics', 'polygraph', 'forensic_linguistics',
                                    'intelligence', 'criminology', 'philosophy_of_science', 'methodology'],
                'key_concept_keys': [
                    'fbi_bau_profiling', 'modus_operandi_vs_signature', 'victimology_fbi_classification',
                    'linkage_analysis', 'geographic_profiling_rossmo', 'statement_analysis_scan',
                    'cognitive_interview', 'reid_technique_critique', 'polygraph_limitations',
                    'forensic_linguistics_idiolect', 'pattern_of_life_intel', 'crime_triangle',
                    'routine_activity_theory', 'falsifiability_in_investigation', 'investigative_pyramid',
                ],
            },
            'reasoning_logic': {
                'domain_zh': '推理與邏輯',
                'schools_covered': ['peirce', 'bayesian', 'parsimony', 'hanlon', 'cognitive_bias',
                                    'anchoring', 'tunnel_vision', 'red_team', 'counterfactual',
                                    'triangulation', 'pre_mortem', 'base_rate'],
                'key_concept_keys': [
                    'abductive_reasoning_peirce', 'bayesian_update_suspect', 'occam_razor_caveat',
                    'hanlon_razor', 'confirmation_bias_investigator', 'anchoring_in_interrogation',
                    'tunnel_vision_systemic', 'devil_advocate_red_team', 'counterfactual_reasoning',
                    'triangulation_evidence', 'pre_mortem_irreversible', 'base_rate_neglect',
                ],
            },
        },
        'teaching_targets': ['self_evidence_audit', 'cross_lobster_case_review', 'pre_bet_decision_quality'],
    },
    'trader': {
        'title': 'trader',
        'title_zh': '交易員',
        'knowledgeDomains_blueprint': {
            'market_structure': {
                'domain_zh': '市場結構',
                'schools_covered': ['order_book', 'auction_theory', 'market_microstructure',
                                    'derivatives', 'short_selling', 'corporate_actions',
                                    'leverage_margin', 'settlement', 'otc_vs_exchange'],
                'key_concept_keys': [
                    'order_types_market_limit', 'order_types_stop_stoplimit', 'bid_ask_spread',
                    'liquidity_depth', 'market_makers_role', 'dark_pools', 'auction_open_close',
                    'circuit_breakers', 'short_selling_mechanics', 'corporate_actions_split_dividend',
                    'derivatives_options_basics', 'derivatives_futures_basics', 'leverage_margin_call',
                    'settlement_t_plus', 'exchange_vs_otc',
                ],
            },
            'technical_analysis': {
                'domain_zh': '技術分析',
                'schools_covered': ['dow_theory', 'price_action', 'volume_analysis', 'moving_averages',
                                    'oscillators', 'candlestick', 'chart_patterns', 'breakouts', 'divergence'],
                'key_concept_keys': [
                    'ta_trend_definition', 'ta_support_resistance', 'ta_volume_confirmation',
                    'ta_moving_average_basics', 'ta_macd', 'ta_rsi', 'ta_bollinger_bands',
                    'ta_kline_basics', 'ta_kline_doji_hammer', 'ta_kline_engulfing',
                    'ta_chart_head_shoulders', 'ta_chart_double_top_bottom', 'ta_chart_triangle_flag',
                    'ta_breakout_failed_breakout', 'ta_divergence',
                ],
            },
            'fundamental_analysis': {
                'domain_zh': '基本面分析',
                'schools_covered': ['graham_value', 'buffett_moat', 'lynch_growth', 'dcf_valuation',
                                    'relative_valuation', 'earnings_quality', 'capital_allocation'],
                'key_concept_keys': [
                    'fa_revenue_growth', 'fa_pe_ratio', 'fa_pb_ratio', 'fa_eps_growth',
                    'fa_free_cash_flow', 'fa_debt_to_equity', 'fa_roe', 'fa_moat_competitive_advantage',
                    'fa_dcf_basics', 'fa_relative_valuation', 'fa_management_quality', 'fa_earnings_quality',
                ],
            },
            'risk_management': {
                'domain_zh': '風險管理',
                'schools_covered': ['kelly_criterion', 'position_sizing', 'stop_loss_discipline',
                                    'diversification', 'tail_risk', 'var_modeling', 'r_multiples',
                                    'drawdown_control', 'correlation_risk'],
                'key_concept_keys': [
                    'risk_per_trade_1_2pct', 'risk_stop_loss_mandatory', 'risk_position_sizing_kelly',
                    'risk_diversification', 'risk_correlation_risk', 'risk_max_drawdown',
                    'risk_no_averaging_down', 'risk_no_revenge_trade', 'risk_var_value_at_risk',
                    'risk_black_swan_tail', 'risk_emergency_exit_plan', 'risk_r_multiple',
                    'risk_risk_reward_ratio', 'risk_max_consecutive_losses', 'risk_stop_after_n_losses',
                ],
            },
            'behavioral_finance': {
                'domain_zh': '行為金融',
                'schools_covered': ['kahneman_tversky', 'thaler', 'shiller_narrative',
                                    'cognitive_bias', 'prospect_theory', 'herd_psychology'],
                'key_concept_keys': [
                    'bf_loss_aversion', 'bf_confirmation_bias', 'bf_overconfidence', 'bf_anchoring',
                    'bf_recency_bias', 'bf_herd_mentality', 'bf_fomo_fear_of_missing',
                    'bf_disposition_effect', 'bf_narrative_fallacy', 'bf_survivorship_bias',
                    'bf_hindsight_bias', 'bf_gambler_fallacy',
                ],
            },
            'trading_workflow': {
                'domain_zh': '交易工作流',
                'schools_covered': ['daily_routine', 'journaling', 'review_audit',
                                    'health_first', 'continuous_learning', 'separation_of_funds'],
                'key_concept_keys': [
                    'wf_pre_market_routine', 'wf_during_market_focus', 'wf_post_market_review',
                    'wf_trading_journal', 'wf_weekly_audit', 'wf_monthly_p_and_l_review',
                    'wf_avoid_news_addiction', 'wf_keep_screen_time_minimal', 'wf_physical_health_first',
                    'wf_continuous_learning_one_book_month', 'wf_separate_account_personal_money',
                ],
            },
        },
        'teaching_targets': ['stock_decision_quality', 'risk_calibration_in_settled_bets',
                             'self_evidence_audit_pre_trade', 'r_multiple_thinking_for_apprentices'],
    },
    # Future occupations — add templates here:
    # 'engineer_scientist': { ... CS/Math/Physics ... },
    # 'investor_analyst':   { ... Finance/Stats/Game theory ... },
    # 'artist_creator':     { ... Aesthetics/Music/Literature ... },
    # 'philosopher':        { ... Epistemology/Ethics/Logic ... },
}


def build_occupation(template_key, kb, lobster_name, declared_by, declared_context,
                     is_first_free_path=False, is_aspirational=False):
    if template_key not in OCCUPATION_TEMPLATES:
        raise SystemExit(f'unknown template: {template_key}')
    tmpl = OCCUPATION_TEMPLATES[template_key]
    knowledge_domains = []
    for domain_key, blueprint in tmpl['knowledgeDomains_blueprint'].items():
        present = [k for k in blueprint['key_concept_keys'] if k in kb]
        depth = 'introductory_broad' if len(present) >= 12 else \
                'introductory_partial' if len(present) >= 5 else \
                'seeded' if len(present) > 0 else 'empty'
        knowledge_domains.append({
            'domain': domain_key,
            'domain_zh': blueprint['domain_zh'],
            'depth': depth,
            'schools_covered': blueprint['schools_covered'],
            'theory_count': len(present),
            'key_concepts': present,
            'started_at': NOW,
            'next_milestone': 'intermediate (need 15+ more theories OR 30 days of active teaching practice)',
        })
    return {
        'version': 1,
        'title': tmpl['title'],
        'title_zh': tmpl['title_zh'],
        'declared_at': NOW,
        'declared_by': declared_by,
        'declared_context': declared_context,
        'is_active': True,
        'is_aspirational': is_aspirational,
        'is_first_free_path': is_first_free_path,
        'knowledgeDomains': knowledge_domains,
        'teaching_targets': tmpl['teaching_targets'],
        'differentiation_tags': ['first_free_path_lobster'] if is_first_free_path else [],
        'updatedAt': NOW,
    }


def main():
    personas = sorted(p.stem for p in MEMORY_DIR.glob('*.json') if p.stem not in SKIP)
    print(f'== Installing occupation schema on {len(personas)} lobsters ==')
    print()

    for name in personas:
        path = MEMORY_DIR / f'{name}.json'
        shutil.copy2(path, path.with_suffix(f'.json.bak_{STAMP}_occupation_schema'))
        mem = json.loads(path.read_text(encoding='utf-8'))
        kb = (mem.get('cogProfile') or {}).get('knowledgeBase') or {}

        existing_occupation = mem.get('occupation')

        if name == 'blazepaw':
            # Migrate aspirationPath → occupation (teacher_scholar)
            aspiration = mem.get('aspirationPath') or {}
            mem['occupation'] = build_occupation(
                template_key='teacher_scholar',
                kb=kb,
                lobster_name='blazepaw',
                declared_by=aspiration.get('declared_by') or 'self_in_one_on_one',
                declared_context=aspiration.get('declared_context') or
                    'blazepaw 主動向 owner 在一對一對話中表達想當老師、研究心理學/社會學/人類學',
                is_first_free_path=True,
                is_aspirational=False,
            )
            if 'aspirationPath' in mem:
                mem['aspirationPath']['superseded_by'] = 'occupation field'
                mem['aspirationPath']['superseded_at'] = NOW
            status = 'TEACHER_SCHOLAR (3 domains, 42 theories)'
        elif name == 'hexclaw':
            aspiration = mem.get('aspirationPath') or {}
            mem['occupation'] = build_occupation(
                template_key='investigator',
                kb=kb,
                lobster_name='hexclaw',
                declared_by=aspiration.get('declared_by') or 'self_in_one_on_one',
                declared_context=aspiration.get('declared_context') or
                    'hexclaw 在一對一對話中選擇調查員。原話: 信心斷線時仍要推進、不能躲程序、不能躲判例、不能躲診斷標準',
                is_first_free_path=False,
                is_aspirational=False,
            )
            if 'aspirationPath' in mem:
                mem['aspirationPath']['superseded_by'] = 'occupation field'
                mem['aspirationPath']['superseded_at'] = NOW
            status = 'INVESTIGATOR (3 domains, 42 theories)'
        elif name == 'pincerbot':
            aspiration = mem.get('aspirationPath') or {}
            mem['occupation'] = build_occupation(
                template_key='trader',
                kb=kb,
                lobster_name='pincerbot',
                declared_by=aspiration.get('declared_by') or 'owner_in_one_on_one',
                declared_context=aspiration.get('declared_context') or
                    'pincerbot 在 5/14 與 Norika 一對一對話中被賦予交易員身份；MMA 規則紀律延伸到交易紀律',
                is_first_free_path=False,
                is_aspirational=False,
            )
            if 'aspirationPath' in mem:
                mem['aspirationPath']['superseded_by'] = 'occupation field'
                mem['aspirationPath']['superseded_at'] = NOW
            status = 'TRADER (6 domains, 80 theories)'
        else:
            # Default empty occupation — preserves choice for future
            if not isinstance(existing_occupation, dict) or not existing_occupation.get('title'):
                mem['occupation'] = DEFAULT_OCCUPATION()
                status = 'default (no profession declared)'
            else:
                status = f'preserved existing: {existing_occupation.get("title")}'

        mem['updatedAt'] = NOW
        path.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding='utf-8')

        # 寫一份 occupation + knowledge_facts 到獨立 file（防止 lobster-agent.js 覆寫）
        OCC_DIR = pathlib.Path('/opt/lobster/lobster-occupations')
        OCC_DIR.mkdir(parents=True, exist_ok=True)
        _occ_file = OCC_DIR / f'{name}.json'
        _occ_data = {
            'occupation': mem.get('occupation') or {},
            'knowledge_facts': {
                k: v for k, v in (kb or {}).items()
                if isinstance(v, dict) and str(v.get('source','')).startswith(
                    ((mem.get('occupation') or {}).get('title') or '') + '_'
                )
            },
            'lobster': name,
            'updatedAt': NOW,
        }
        _occ_file.write_text(json.dumps(_occ_data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'  {name:14s}  {status}  → also written to {_occ_file}')

    print()
    print(f'== schema installed on {len(personas)} lobsters ==')
    print(f'   blueprint templates available: {list(OCCUPATION_TEMPLATES.keys())}')
    print(f'   to add new template: edit OCCUPATION_TEMPLATES in this file')

if __name__ == '__main__':
    main()
