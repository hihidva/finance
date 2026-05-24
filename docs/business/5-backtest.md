# Module 5: Backtest (Sanity check rule engine)

## 5.1 Mục đích

Replay rule engine trên window lịch sử để verify:
- Rule engine không có bug (vd: divide-by-zero, NaN propagate).
- Tier A signal có expectancy dương hay không trên data thực.
- Win rate, max drawdown, average P&L theo từng ticker.

**Không backtest LLM arbiter** — vì LLM stochastic và slow. Backtest chỉ chạy phần rule-based (analyze() + risk plan).

**Đầu vào:** window `[start, end]`, optional symbols list.

**Đầu ra:**
- `TickerStats` per asset: `count`, `tier_a_count`, `win_rate`, `avg_pnl_7d`, `max_drawdown`, `expectancy_7d`
- Summary table in console
- Optional CSV dump tất cả signals (để phân tích sâu trong Excel/pandas)

## 5.2 Entities

Không có DB table riêng — backtest tính in-memory, không persist (tránh nhiễu data thật).

### TickerStats (in-memory dataclass)

| Trường | Kiểu | Mô tả |
|---|---|---|
| `symbol` | str | |
| `count` | int | Tổng signal phát ra trong window |
| `tier_a_count` | int | Chỉ Tier A |
| `tier_a_buy_count`, `tier_a_sell_count` | int | |
| `win_rate_7d` | float | % signal có pnl_7d > 0 |
| `avg_pnl_7d` | float | Mean P&L |
| `max_pnl_7d`, `min_pnl_7d` | float | |
| `max_drawdown_pct` | float | Worst max_adverse trong tất cả signal |
| `expectancy_7d` | float | win_rate × avg_win + (1-win_rate) × avg_loss |

## 5.3 Quy trình

[jobs/backtest.py](../../src/finance_bot/jobs/backtest.py):

```
For each symbol in (symbols or watchlist.primary_assets):
    df = load_ohlcv_df(asset_id, "1d", limit=very_large) filtered by [start, end]
    if len(df) < 60: skip (warmup chưa đủ)

    For i in range(60, len(df) - max_horizon_bars):
        window = df.iloc[:i+1]                    # KHÔNG leak future
        draft = analyze(asset_cfg, window, wl)    # rule engine only
        if draft.tier == "C" and draft.side == "hold": continue (skip noise)

        entry_price = _entry_price(asset_cfg, df, i)
            # VN: df.iloc[i+1].open (next bar ATO), nếu i+1 không có → skip
            # Crypto/Commodity: window.iloc[-1].close (immediate)

        For h in (24, 72, 168, 720):
            target_idx = find bar at +h hours
            if not found: skip horizon
            exit_price = df.iloc[target_idx].close
            pnl_pct = compute (đảo dấu sell)

        Append (signal_dict, outcomes_dict) vào list

    Compute TickerStats(symbol, signals, outcomes)

Print summary table
If output_csv: dump (signal × outcomes) flat CSV
```

**Future leak prevention**: window là `df.iloc[:i+1]` — analyze() chỉ thấy bar tới `i`. Outcome dùng bar > `i` (T+1 cho VN, T+0 close cho immediate; +h cho horizon).

## 5.4 Default config

| Tham số | Giá trị |
|---|---|
| Warmup | 60 bars (đủ EMA200 chưa? Không, cần ≥ 200 — TODO check) |
| Horizons | 24h, 72h, 168h, 720h |
| Symbols | All `primary_assets` nếu không truyền `--symbols` |
| Window | required `--start`, `--end` (YYYY-MM-DD) |

## 5.5 Validation rules

- `start < end`, format ISO `YYYY-MM-DD`.
- Mỗi symbol phải có ≥ 60 bars trong window — nếu không, log warning + skip.
- VN ATO: nếu `i` là bar cuối cùng trong df, không có `i+1` → skip signal đó.
- Horizon bar: tìm bar có `ts >= signal_ts + h` đầu tiên — nếu cuối df không có → skip horizon đó.

## 5.6 Edge cases

- **Holiday dài** (Tết 7-9 ngày): horizon 24h có thể fall vào ngày nghỉ → tìm bar tiếp theo, P&L sẽ là 24h+.
- **Asset có gap giá** (split, dividend chưa adjust): vnstock đã adjust → OK; nhưng nếu data dirty → P&L sẽ bị skewed. Không có guard — user phải verify data trước.
- **Tier A mỏng**: nhiều symbol có thể chỉ có vài signal Tier A trong 1 năm — `win_rate` không có ý nghĩa thống kê. Báo cáo `count` để user judgment.
- **CSV output trùng path**: overwrite, không cảnh báo.

## 5.7 CLI

```bash
backtest --start 2024-01-01 --end 2026-04-30
backtest --start 2024-01-01 --end 2026-04-30 --symbols FPT,HPG --output bt.csv
```

Cron: CN 09:00 chạy backtest 1 năm gần nhất, log vào `logs/cron.log`. Lưu ý cron escape `\%` (xem [cron.example](../../cron.example)).

## 5.8 Phân quyền

Không persist data — không cần phân quyền. Chỉ cần read access vào `prices` + `assets`.
