# Module 3: Closed-loop Learning (Vòng học khép kín)

## 3.1 Mục đích

Bot phải "thông minh hơn từng ngày" — học từ kết quả thực tế của signal đã alert + phản hồi user qua Telegram button. Không cần fine-tune model; thay vào đó dùng RAG: re-embed `signal + outcome + user_decision` vào ChromaDB để run kế tiếp `arbitrate()` retrieve case lịch sử tương tự.

**Đầu vào:**
- `signals` đã có sau ≥1 horizon (24h)
- `prices` đã có bar sau `expected_entry_at`
- Telegram callback queue (user click button)

**Đầu ra:**
- `outcomes` table — P&L thực tế tại 1d/3d/7d/30d
- `signals.user_decision` — `entered` / `skipped` / NULL
- ChromaDB `signals_history` collection — re-embedded với outcome + decision

## 3.2 Entities

### `outcomes`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id` | int PK | |
| `signal_id` | FK | → signals.id |
| `horizon_hours` | enum | 24, 72, 168, 720 (= 1d/3d/7d/30d) |
| `entry_price` | decimal | Giá vào thực tế (next bar open cho VN ATO, close cho immediate) |
| `exit_price` | decimal | Close tại horizon |
| `pnl_pct` | decimal(8,4) | (exit - entry) / entry × 100 (đảo dấu cho `sell`) |
| `max_favorable_pct` | decimal | Best swing trong window |
| `max_adverse_pct` | decimal | Worst swing trong window |
| `evaluated_at` | datetime | Khi nào ghi outcome này |

PK soft = `(signal_id, horizon_hours)` — re-evaluate sẽ overwrite.

### `signals.user_decision` (extend Module 2 schema)

| Trường | Kiểu | Mô tả |
|---|---|---|
| `user_decision` | enum nullable | `entered`, `skipped` |
| `user_decision_at` | datetime nullable | Khi user click button |
| `notification_message_id` | int nullable | Để edit reply markup sau khi click |

## 3.3 Quy trình

### Eval outcomes

[jobs/eval_outcomes.py](../../src/finance_bot/jobs/eval_outcomes.py):

```
HORIZONS_HOURS = (24, 72, 168, 720)

for each signal where notified=True and side in (buy, sell):
    for each h in HORIZONS_HOURS:
        if outcome already exists for (signal, h): skip
        target_ts = expected_entry_at + h hours
        if no bar at >= target_ts: skip (chưa đủ thời gian)
        entry_price = next_bar.open (VN ATO) or signal.price_at_signal (immediate)
        exit_price = bar.close at target_ts
        pnl_pct = compute (đảo dấu cho sell)
        max_favorable, max_adverse = scan bars trong window
        insert outcome
    if any new outcome inserted:
        re-embed signal+all_outcomes+user_decision vào ChromaDB.signals_history
```

**Idempotent**: chạy mỗi sáng 06:00 — chỉ thêm outcome chưa có, không double-count.

### Process feedback

[jobs/process_feedback.py](../../src/finance_bot/jobs/process_feedback.py):

```
offset = read .telegram_offset.json (last update_id processed + 1)
updates = telegram.getUpdates(offset, timeout=0, allowed_updates=["callback_query"])
for each callback_query:
    parse callback_data → (action, signal_id)  # "act:enter:123" → ("enter", 123)
    set_user_decision(signal_id, "entered" | "skipped")
    answer_callback_query (ack)
    edit_message_reply_markup(reply_markup=None)  # remove buttons
    update offset = update_id + 1
write .telegram_offset.json
```

**Telegram retention**: callback queue giữ tối đa 24h ở phía Telegram. Nên `process-feedback` phải chạy ≥ 1 lần/ngày — đã được gọi tự động ở đầu `run-signals` (16:00 T2-T6, 06:00 daily).

### RAG re-embed

[ai/memory.py](../../src/finance_bot/ai/memory.py) `remember_signal_outcome()`:

```
SignalCase = signal + list[outcome] + user_decision
text = build_text(case)  # gồm:
  - "Tín hiệu BUY tier A cho FPT ngày 2026-04-15..."
  - "Indicators: RSI=28, MACD bullish cross, ..."
  - "Outcome: 1d=+1.2%, 3d=+3.8%, 7d=+5.1%, 30d=+8.9%"
  - "User đã VÀO LỆNH thực tế" / "User BỎ QUA" / "User chưa phản hồi"
embedding = embed(text)
chroma.upsert(id=signal_id, embedding, metadata={asset_class, side, tier, ...})
```

Run kế tiếp `arbitrate()` retrieve top-5 similar past signals, đưa vào prompt để LLM rút kinh nghiệm.

## 3.4 Validation rules

- Outcome chỉ tính khi có `next_bar` sau `expected_entry_at` (VN ATO) — nếu cuối tuần / holiday, đợi tới phiên kế.
- `pnl_pct` luôn tính từ góc nhìn người vào lệnh: `buy` → `(exit-entry)/entry`, `sell` → `(entry-exit)/entry`.
- `user_decision` có thể NULL vĩnh viễn nếu user không click — RAG vẫn re-embed với note "chưa phản hồi".
- `notification_message_id` NULL → không edit reply markup được, vẫn ghi `user_decision` bình thường.

## 3.5 Edge cases

- **Telegram offset bị reset** (xoá `.telegram_offset.json`): có thể xử lý lại update đã xử lý → idempotent vì `set_user_decision` overwrite cùng giá trị.
- **User click 2 lần**: cả 2 đều process; lần sau overwrite — không hại.
- **Signal bị xoá khỏi DB nhưng callback đến**: `set_user_decision` raise → catch + log warning, ack callback để Telegram không retry.
- **Outcome retroactive (fix data lịch sử)**: chạy `eval-outcomes` lần nữa, re-embed lại Chroma — id giữ nguyên, embedding mới.
- **Asset hết niêm yết**: không có `prices` mới → `eval-outcomes` skip, log "chưa đủ data".

## 3.6 CLI

```bash
eval-outcomes               # tính outcome + re-embed (idempotent)
process-feedback            # poll Telegram callback (cũng tự gọi ở đầu run-signals)
```

## 3.7 Schedule

| Local time | Job | Note |
|---|---|---|
| 06:00 daily | `eval-outcomes` | Đảm bảo outcome 1d được tính sau 1 đêm |
| 16:00 T2-T6 | `run-signals` (tự gọi `process-feedback` đầu pipeline) | User feedback vào DB trước khi compute signal mới |
| Optional | `process-feedback` mỗi 15 phút | Uncomment trong cron.example nếu muốn realtime hơn |
