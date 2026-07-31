# Research Feedback Loop — 2026-05-03

Status: active system contract
Created: 2026-05-03T14:01:49Z

This document feeds paper-methods questions back into the Lobster substrate as engineering requirements. Its purpose is to prevent the system from remaining a patchwork of disconnected dashboards, memories, simulations, and narrative reports.

## 1. Canonical Research Framing

The current paper should not call the relational count matrix "cooperation." The observed unit is directed listening exposure / attention flow:

```text
source agent heard target agent N times
```

Recommended paper framing:

- Layer 1: directed listening exposure / attention flow.
- Layer 2: directed social influence / trust proxy.
- Layer 3: Tiamat team outcome.

Suggested title family:

```text
Listening-Trust Asymmetry and Team Outcomes in a Multi-Agent Substrate
```

## 2. Outcome Dataset Decision

Primary outcome dataset:

```text
Tiamat sandbox simulation
120 triples x 12 seeded trials = 1440 simulated raid outcomes
```

Robustness / ecological-validity dataset:

```text
Live raid memory summary
Completed raids from /root/lobster-memory, deduped by bet_id/match.
Use as cohort-level robustness check only unless complete 120-triple coverage is later reconstructed.
```

Rationale:

- The sandbox outcome layer is controlled: every possible 3-agent team has the same sample size.
- Live raid memory is ecologically valid but imbalanced and not guaranteed to cover all 120 triples.
- Mixing them as one outcome layer would create sample-selection bias.

Methods wording:

```text
The primary outcome layer is based on a controlled Tiamat sandbox simulation rather than the live substrate state. Each of the 120 possible three-agent teams was evaluated across 12 seeded raid trials, yielding 1,440 simulated outcomes. Live raid memory was used only as an ecological-validity robustness check at the cohort level.
```

## 3. Directed Listening Exposure Contract

Canonical field:

```text
memory.listeningProfile.bySpeaker[target]
```

Unit:

```text
raw directed passive-listening exposure count
```

Definition:

- Row/source = listener.
- Column/target = speaker heard by the listener.
- One increment corresponds to one heard lounge message from that target in the passive-listening loop.
- It is not weighted by sentiment, response quality, agreement, or outcome.
- It should not be labeled cooperation unless a future cooperation-specific metric is implemented.

Current exported file:

```text
/root/lobster-methods-export/directed_coop_matrix_raw_bySpeaker.csv
```

Rename target:

```text
directed_attention_matrix_raw_bySpeaker.csv
```

## 4. Trust / Social Influence Contract

Canonical stored field:

```text
memory.localBrain.socialInfluence[target]
```

This is not Paper 5 Section 4.1 formal trust. It is a runtime social-influence / derived-trust proxy.

Implementation target before EMA:

```text
socialInfluence_target =
  opinion01 * 0.35
+ trust * 0.35
+ interactions01 * 0.30

opinion01 = clamp((socialMap.opinion + 5) / 10, 0, 1)
trust = socialMap.trust if present else 0.5
interactions01 = clamp(socialMap.interactions / 20, 0, 1)
```

Current limitation:

- `socialMap.trust` is usually absent, so the trust term defaults to 0.5.
- `socialMap.opinion` and `socialMap.interactions` saturate quickly under dense passive listening.
- Live 2026-05-03 trust/socialInfluence matrix is degenerate: all main cohort directed edges are approximately 0.825.

Paper treatment:

```text
Because the directed trust proxy saturated across agents by the analysis window, it was retained as a substrate diagnostic but excluded from fine-grained alignment tests. The primary relational layer used in the analysis is therefore directed listening exposure.
```

Discussion wording:

```text
The saturation itself may indicate a substrate-level ceiling effect in repeated passive exposure systems, but distinguishing genuine trust convergence from metric saturation requires a longer-window or reparameterized trust model.
```

## 5. Exported Methods Dataset

Current export directory:

```text
/root/lobster-methods-export/
```

Files:

- `directed_trust_matrix_localBrain_socialInfluence.csv`
- `directed_trust_matrix_reconstructed_target.csv`
- `directed_coop_matrix_raw_bySpeaker.csv`
- `tiamat_120_triple_records.csv`
- `research_contract.json`

Data interpretation:

- `tiamat_120_triple_records.csv` is sandbox simulation, not live memory.
- `games=12` for every triple.
- `directed_coop_matrix_raw_bySpeaker.csv` should be interpreted as listening exposure / attention flow.
- Trust matrix is a diagnostic/caveat unless reparameterized.

## 6. Required System Integration Backlog

### P0 — Naming and Data Contract

- Rename dashboard/research labels from `coop` to `attention_flow` or `listening_exposure`.
- Keep backward-compatible aliases temporarily so existing dashboards do not break.
- Add `research_contract.json` as the canonical machine-readable methods layer.

### P1 — Outcome Layer Split

- Dashboard must visually separate:
  - `controlled_simulation_outcome`
  - `live_memory_outcome`
- Do not present live raid memory as complete 120-triple coverage unless all triples are present with sample sizes.

### P1 — Trust Ceiling Diagnostic

- Add a trust saturation warning to dashboard/research export when:

```text
stddev(directed_trust_edges) < 0.01
```

- Label as metric ceiling effect, not as fine-grained trust alignment.

### P2 — Live Raid Triple Coverage

- Build a live-memory 120-triple coverage table:
  - wins
  - losses
  - completed
  - total_seen
  - missing flag
  - sample threshold flag

- Until complete coverage exists, use live memory only as robustness summary.

### P2 — Research Dashboard Panel

Add a dashboard panel titled:

```text
Research Methods Contract
```

It should expose:

- Primary outcome dataset.
- Robustness dataset.
- Unit definitions for listening exposure and trust proxy.
- Trust saturation warning.
- Paths / API endpoints for exported matrices.

## 7. Design Principle

Every future paper question should either:

1. map to an existing canonical field and unit, or
2. create a new named field with a unit, range, update rule, and dashboard visibility.

If neither is true, the system should reject the metric as a narrative-only construct.
