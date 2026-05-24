# Module 2: Signal Pipeline (Sinh tín hiệu giao dịch)

## 2.1 Mục đích

Module cốt lõi: từ OHLCV + news + macro context, ra **Tier A** signal và alert qua Telegram. Implement strict invariants để tránh tín hiệu rác.

**Đầu vào:**
- `prices` (OHLCV ≥ 60 bars) từ Module 1
- `news` (48h gần nhất) lọc theo keyword asset
- Macro context (DXY, WTI close + % change 7d/30d)
- RAG: top-5 similar past signals + top-4 relevant knowledge entries

**Đầu ra:**
- 1 row trong `signals` table (mọi tier)
- Telegram message + 2 inline button (chỉ Tier A + side ≠ hold + chưa cooldown)

## 2.2 Strategy đã chốt

| Yếu tố | Giá trị |
|---|---|
| Khung thời gian | 1D (daily) |
| Vị thế dự kiến | Swing/position vài tuần đến vài tháng |
| Tier A threshold | `agree_ratio ≥ 0.60` (≥ 60% indicators agree), confidence ≥ 0.75, news không ngược chiều |
| Tier B | `agree_ratio ≥ 0.45` (≥ 45% indicators agree), confidence ≥ 0.60 |
| Tier C | còn lại |

> Ngưỡng dùng **tỷ lệ động** (`agree_count / len(snapshot.votes)`) — tự co dãn khi catalog thêm/bớt indicator. Với 14 indicator hiện tại: Tier A cần ≥ 9 agree (≈60%), Tier B cần ≥ 7 agree (≈50% sau làm tròn). Config tại [config/watchlist.yaml](../../config/watchlist.yaml) (`signal.tier_a.min_agree_ratio`).
| Cooldown | 1 alert / ticker / 24h, bất kể side |
| Alert channel | Telegram text-only + 2 inline button feedback |

## 2.3 Indicators (rule engine)

14 indicator thuần pandas trong [analysis/technical.py](../../src/finance_bot/analysis/technical.py):

| Indicator | Buy vote | Sell vote |
|---|---|---|
| RSI(14) | < 30 (oversold) | > 70 (overbought) |
| MACD(12,26,9) | line > signal & histogram > 0 | line < signal & histogram < 0 |
| EMA cross 20/50 | EMA20 > EMA50 + crossing up | EMA20 < EMA50 + crossing down |
| EMA trend 50/200 | close > EMA200 & EMA50 > EMA200 | close < EMA200 & EMA50 < EMA200 |
| Bollinger(20, 2σ) | close < lower band | close > upper band |
| Volume spike | volume > 1.5 × MA20(volume) + close > prev close | volume > 1.5 × MA20 + close < prev close |
| ATR breakout | close > prev close + 1.5 × ATR | close < prev close − 1.5 × ATR |
| Ichimoku Cloud | close > Kumo top & Tenkan > Kijun | close < Kumo bot & Tenkan < Kijun |
| ADX(14) | ADX > 25 & DI+ > DI− | ADX > 25 & DI− > DI+ |
| Supertrend(10, 3) | direction = +1 (flip mới = strength cao) | direction = −1 (flip mới = strength cao) |
| OBV(20) | OBV trending up (divergence vs giá khi giá xuống) | OBV trending down |
| Donchian(20) | close > prev 20-bar high (breakout up) | close < prev 20-bar low (breakout down) |
| MFI(14) | MFI < 20 và đang hồi (oversold reversal) | MFI > 80 và đang giảm (overbought reversal) |
| CMF(20) | CMF > +0.05 và đang tăng (accumulation) | CMF < −0.05 và đang giảm (distribution) |

`TechSnapshot.dominant_side` = `buy` nếu `buy_count > sell_count`, ngược lại `sell`. `agree_count = max(buy_count, sell_count)`.

> Catalog chi tiết (công thức, min_bars, horizon, vote rule, edge cases, lifecycle khi thêm/bớt indicator) — xem [Module 8: Indicators Catalog](8-indicators-catalog.md).

## 2.4 Risk plan (ATR-based)

[analysis/risk.py](../../src/finance_bot/analysis/risk.py) tính:

- `entry = close[-1]`
- `stop_loss = entry ± atr_mult × ATR(14)` (default `atr_mult=2.0`), hoặc swing high/low gần nhất nếu chặt hơn (giảm risk).
- `take_profit = entry ± rr × |entry − stop_loss|` (default `rr=2.5`).
- `R:R = 1:2.5`.

## 2.5 Entry window

| Asset class | `entry_window` | `expected_entry_at` |
|---|---|---|
| `vn_stock` | `ato_next_session` | Next VN session ATO (skip weekend) |
| `crypto`, `commodity` | `immediate` | NULL |

VN: signal lúc 16:00 → khớp ATO 9:00 hôm sau. Logic ở `_next_vn_ato_at()` trong [analysis/signal.py](../../src/finance_bot/analysis/signal.py).

## 2.6 LLM Final Arbiter (invariants)

[ai/arbiter.py](../../src/finance_bot/ai/arbiter.py) gọi Claude (qua local CLI `claude --print`, model mặc định `claude-opus-4-7`):

```
prompt = SYSTEM_PROMPT (Vietnamese, ràng buộc không up-tier)
       + asset block (symbol, asset_class, name)
       + draft block (side/tier/confidence/rationale)
       + indicators block (TechSnapshot)
       + risk block (entry/SL/TP)
       + news block (last 48h, max 8)
       + macro block (DXY, WTI: last_close + %7d + %30d)
       + similar_cases block (top-5 RAG signals_history)
       + knowledge_snippets block (top-4 RAG knowledge)

LLM trả JSON: {side, tier, confidence, reasoning, news_against}
```

**Invariants** (phải giữ nguyên khi sửa code):

- `_TIER_RANK = {"A": 3, "B": 2, "C": 1}`. Nếu `llm_tier_rank > draft_tier_rank` → **reject**, ép về `draft.tier`. LLM chỉ được CONFIRM hoặc HẠ tier.
- Nếu `llm_side != draft.side` → ép `side="hold"`, `tier="C"`. LLM không được flip side.
- Nếu Claude CLI không có / exit non-zero / JSON parse fail → giữ `draft` nguyên, ghi reason vào `llm_reasoning`.
- `context_only` asset: arbiter **short-circuit** trả về `hold/C` ngay (không gọi LLM).

## 2.7 Persist + alert

Pipeline trong [jobs/run_signals.py](../../src/finance_bot/jobs/run_signals.py):

```
_pull_feedback_safely()              # Module 3 — đầu pipeline
for each primary_asset:
    open session 1 → load OHLCV, build news+macro briefs
    close session 1
    arbitrate(draft, news, macro)    # slow, outside session
    open session 2:
        insert_signal(row)
        if final.tier == "A" and final.side != "hold":
            if not latest_alerted_signal(within=24h):
                send Telegram alert
                mark_signal_notified(message_id)
```

**Mọi signal đều ghi DB** (kể cả Tier B/C, kể cả `context_only` ép hold) — training data cho RAG sau này.

### `signals` table key columns

| Trường | Mô tả |
|---|---|
| `tier` | `A`, `B`, `C` |
| `side` | `buy`, `sell`, `hold` |
| `confidence` | decimal(4,3) — final after arbiter |
| `price_at_signal` | close[-1] |
| `entry_window` | `immediate`, `ato_next_session` |
| `expected_entry_at` | nullable datetime |
| `stop_loss`, `take_profit` | decimal nullable (NULL nếu hold) |
| `indicators` | JSON: 7 indicator votes + `draft_tier`, `draft_confidence` |
| `news_context` | JSON: `news_against` flag |
| `rag_context` | JSON: `similar_cases`, `knowledge_used` (metadata + score) |
| `llm_model`, `llm_reasoning` | tracking |
| `notified` | bool — đã gửi Telegram chưa |
| `notification_message_id` | int — để edit reply markup khi user click |
| `user_decision` | enum `entered`, `skipped`, NULL |
| `user_decision_at` | datetime |

## 2.8 Telegram alert format

Plain text tiếng Việt, kèm 2 inline button:

```
🔔 [FPT] BUY  Tier A
Confidence: 0.82   |   4/7 indicators agree (buy)
Entry: 125,300đ (ATO phiên kế tiếp 2026-05-05 09:00 ICT)
SL:    119,800đ   |   TP: 138,750đ   |   R:R = 1:2.5

LLM: Đồng thuận — RSI hồi từ oversold, MACD cross dương, volume tăng 1.8x, không có news ngược chiều...

[ ✅ Đã vào lệnh ]   [ ⏭ Bỏ qua ]
```

Callback data: `act:enter:<signal_id>` / `act:skip:<signal_id>` (xem [notifier/telegram.py](../../src/finance_bot/notifier/telegram.py)).

## 2.9 CLI

```bash
run-signals                 # all primary assets
run-signals --symbol FPT    # 1 asset (refuses context_only)
```

## 2.10 Composite Score Alert Engine (3 thước đo trọng số bằng nhau)

> Section này nâng cấp pipeline từ "rule engine vote → Tier" sang **"3 evaluation service → composite score → Tier"** với mỗi service đóng góp **trọng số 1/3**. Tier vẫn còn (vẫn cần cho cooldown + alert decision), nhưng cách tính khác hẳn.

### 2.10.1 Lý do tách 3 service

Pipeline cũ chỉ dùng 14 technical vote → Tier. Macro context chỉ là input cho LLM, micro fundamentals hoàn toàn chưa có. Khi muốn alert chính xác hơn → phải đưa cả 3 chiều (technical / macro / micro) vào quyết định một cách **có thể đo lường được**, không chỉ "LLM tự thấy".

3 service mới:

| Service | Module | Score range | Owner data |
|---|---|---|---|
| Technical Evaluation | [Module 9](9-technical-evaluation.md) | `[-1, +1]` | OHLCV (đã có) |
| Macro Evaluation | [Module 10](10-macro-evaluation.md) | `[-1, +1]` | DXY, WTI, FFR, 10Y (DXY+WTI đã có; FFR+10Y phải bổ sung Module 1) |
| Micro Evaluation | [Module 11](11-micro-evaluation.md) | `[-1, +1]` | Fundamentals (ROA/ROE/P/E/P/B) + industry avg + news sentiment (news đã có; còn lại phải bổ sung Module 1) |

### 2.10.2 Aggregation formula (composite score)

```
WEIGHT_TECH  = 1/3
WEIGHT_MACRO = 1/3
WEIGHT_MICRO = 1/3

scores = []
if tech_score.score  is not None: scores.append((WEIGHT_TECH,  tech_score.score))
if macro_score.score is not None: scores.append((WEIGHT_MACRO, macro_score.score))
if micro_score.score is not None: scores.append((WEIGHT_MICRO, micro_score.score))

if not scores:
    composite = 0.0    # tất cả service đều thiếu data → neutral, sẽ ra Tier C
    side = "hold"
else:
    total_w = sum(w for w, _ in scores)
    composite = sum(w * s for w, s in scores) / total_w
    side = "buy" if composite > 0 else ("sell" if composite < 0 else "hold")
```

**Tính chất:**

- `composite ∈ [-1, +1]` đảm bảo bởi mỗi sub-score đã ∈ [-1, +1].
- Khi 1 service thiếu data (score = None) → weight tự re-normalize sang 2 service còn lại. **Không penalty**, không treat 0 (treat 0 sẽ bias về neutral một cách sai).
- Khi 2+ service đồng thuận mạnh cùng chiều → composite tự nhiên cao; khi 2 chiều mâu thuẫn → composite kéo về 0 (đúng tinh thần "3 thước đo cần đồng thuận").

### 2.10.3 Tier mapping mới

Tier dựa trên **|composite|** và **số service đồng thuận**:

| Điều kiện | Tier |
|---|---|
| `\|composite\| ≥ 0.60` VÀ ≥ 2 service cùng dấu với composite VÀ `news_against == False` | **A** |
| `\|composite\| ≥ 0.40` VÀ ≥ 2 service cùng dấu với composite | **B** |
| `\|composite\| ≥ 0.40` nhưng chỉ 1 service đồng thuận | **C** |
| `\|composite\| < 0.40` | **C** |

> Threshold (0.60, 0.40) là config ở [config/watchlist.yaml](../../config/watchlist.yaml) (`signal.tier_a.min_composite`, `signal.tier_b.min_composite`).
> "≥ 2 service cùng dấu" = đếm số service có `sign(score) == sign(composite)`. Service `score = None` không count cả hai phía.

### 2.10.4 Quan hệ với LLM Final Arbiter (§2.6)

Composite score chạy **trước LLM**, LLM giữ vai trò đúng như cũ — confirm hoặc HẠ tier, không up-tier. Cụ thể:

```
draft = composite_engine(tech, macro, micro)   # draft.tier ∈ {A, B, C}, draft.side
arb   = arbitrate(draft, news, macro, ...)     # LLM xem cả 3 score + RAG → confirm / hạ tier
final = arb.decision                           # tier ≤ draft.tier (invariant)
```

LLM prompt được mở rộng để show cả 3 score (xem [ai/prompt.py](../../src/finance_bot/ai/prompt.py) — section "EVALUATION SCORES"). LLM thấy 3 con số rõ ràng → phán đoán "cần hạ tier không" có grounding tốt hơn so với chỉ nhìn raw indicators như cũ.

**Invariants giữ nguyên** (xem §2.6) — KHÔNG đổi.

### 2.10.5 Backwards compatibility với RAG

- `signals.indicators` JSON bổ sung 3 trường mới: `tech_score`, `macro_score`, `micro_score` (cùng `score`, `breakdown`).
- `evaluation_version` field mới: `v2` cho signal sinh từ composite engine, `v1` cho signal cũ.
- RAG `signals_history` similarity search filter `evaluation_version == v2` khi serve cho draft mới → tránh trộn pattern cũ/mới làm nhiễu.
- Migration: signals cũ vẫn nguyên trong DB, chỉ tag `v1`. Không backfill score.

### 2.10.6 Edge cases (Composite Engine)

| Edge case | Hành vi |
|---|---|
| Cả 3 service đều `score = None` (cực biên) | `composite = 0.0`, `side = "hold"`, `tier = "C"`. Vẫn ghi DB cho training. |
| 2 service cùng buy, 1 service không có data | Composite = avg 2 service. Đếm "service đồng thuận" = 2 → có thể Tier A nếu mạnh. |
| 3 service cùng dấu nhưng `\|composite\| < 0.40` | Tier C (chưa đủ mạnh) — đúng tinh thần "đồng thuận nhưng yếu vẫn không alert". |
| `tech_score = +0.9`, `macro_score = -0.9`, `micro_score = +0.0` | Composite ≈ 0, side = "hold", tier = "C". Mâu thuẫn rõ → không alert. |
| Asset `context_only=True` | Arbiter short-circuit (§2.6); Composite Engine cũng không chạy. |
| `news_against = True` từ Module 11 | Tier A bị gác xuống B (giống rule cũ ở §2.2). |

### 2.10.7 CLI mới (option)

```bash
uv run python main.py show-scores --symbol FPT
# Dump 3 score + composite + draft tier — KHÔNG ghi DB, KHÔNG alert. Debug only.
```

### 2.10.8 Triển khai theo phase

| Phase | Phạm vi | Trạng thái |
|---|---|---|
| Phase 1 | Module 9 (technical eval extracted) + Composite Engine với 1/3 × tech + 1/3 × macro (DXY/WTI) + 1/3 × micro chỉ news sentiment | Sẵn sàng implement (data đủ) |
| Phase 2 | Module 1 fetch fundamentals + industry avg → Module 11 đủ 3 sub-component | Cần extend Module 1 |
| Phase 3 | Module 1 fetch FFR + 10Y UST → Module 10 đủ 4 macro indicator | Cần FRED API key |

Phase 1 đã có thể bật ngay với equal-weight composite. Phase 2/3 chỉ làm Module 11/10 mạnh hơn, không phá interface composite.

## 2.11 Edge cases

- **OHLCV < 60 bars**: skip asset, log warning "chưa đủ chạy indicators (cần >=60). Hãy chạy `sync-prices` trước."
- **All indicators flat (no buy/sell)**: `dominant_side="hold"`, Tier C.
- **News kéo ngược dominant_side**: arbiter set `news_against=True`, Tier max = B (không qua được Tier A threshold).
- **VN holiday next day**: `_next_vn_ato_at()` skip weekend; chưa check VN public holiday calendar — TODO nếu cần chính xác hơn.
- **Cooldown chặn alert**: vẫn ghi signal vào DB, chỉ skip Telegram. Log info "đã alert lúc X (Yh trước)".
- **Telegram offline**: `send_alert` trả `None`, signal không mark notified → run kế tiếp có thể retry (nhưng sẽ bị cooldown nếu prior signal khác đã notified).
