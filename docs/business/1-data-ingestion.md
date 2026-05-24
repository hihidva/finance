# Module 1: Data Ingestion (Ingest dữ liệu thị trường)

## 1.1 Mục đích

Đồng bộ dữ liệu thị trường vào MySQL để pipeline signal có nền tảng tin cậy. Chỉ ingest, không phân tích — phân tích thuộc Module 2 (Signal Pipeline).

**Đầu vào:**
- vnstock 3.x (VCI/TCBS source) → VN stock OHLCV + foreign flows + corporate events
- ccxt (Binance) → crypto OHLCV
- yfinance → XAU/USD, WTI (CL=F), DXY (DX-Y.NYB)
- RSS feeds → news

**Đầu ra (MySQL tables):**
- `prices` — OHLCV theo `(asset_id, timeframe, ts)`
- `vn_flows` — foreign buy/sell + proprietary + margin (chỉ VN)
- `corporate_events` — GDKHQ, cổ tức, phát hành thêm (chỉ VN)
- `news` — RSS đã chuẩn hoá (title, source, published_at, summary, lang, link)
- `fetch_log` — debug & gap detection

## 1.2 Entities

### `assets`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id` | int PK | Auto-increment |
| `symbol` | varchar(32) UNIQUE | `FPT`, `BTC/USDT`, `XAU/USD`, `DX-Y.NYB`... |
| `name` | varchar(128) | Human-readable |
| `asset_class` | enum | `vn_stock`, `crypto`, `commodity`, `macro` |
| `source` | varchar(32) | `vnstock`, `ccxt`, `yfinance` |
| `context_only` | boolean | True = chỉ feed LLM, không sinh signal (DXY, WTI) |
| `meta` | JSON | Source-specific (vd: ccxt exchange) |

### `prices`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `asset_id` | FK | → assets.id |
| `timeframe` | enum | `1d`, `1h`, ... (hiện chỉ dùng `1d`) |
| `ts` | datetime | UTC bar open time |
| `open/high/low/close` | decimal(20,8) | OHLC |
| `volume` | decimal(24,4) | Volume |

PK = `(asset_id, timeframe, ts)`. Upsert `ON DUPLICATE KEY UPDATE`.

### `vn_flows`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `asset_id` | FK | Chỉ VN stock |
| `ts` | date | EOD date |
| `foreign_buy_value` | decimal | Khối ngoại mua (VND) |
| `foreign_sell_value` | decimal | Khối ngoại bán (VND) |
| `prop_buy_value` | decimal | Tự doanh mua (nullable, vnstock 3.x không stable) |
| `prop_sell_value` | decimal | Tự doanh bán (nullable) |
| `margin_outstanding` | decimal | Dư nợ margin (nullable) |

### `corporate_events`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `asset_id` | FK | Chỉ VN stock |
| `event_type` | enum | `ex_rights`, `cash_dividend`, `stock_dividend`, `share_issue`, `agm`, `other` |
| `ex_date` | date | GDKHQ date |
| `record_date` | date nullable | |
| `ratio` | varchar(32) nullable | "10:1", "5%", v.v. |
| `description` | text | |

### `news`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `source` | varchar(64) | RSS feed name (CafeF, VnEconomy, ...) |
| `title` | varchar(512) | |
| `link` | varchar(1024) UNIQUE | URL gốc — dedup key |
| `summary` | text nullable | |
| `published_at` | datetime | |
| `lang` | enum | `vi`, `en` |
| `tags` | JSON | Source-defined tags |

### `fetch_log`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `job` | varchar(64) | `sync_prices`, `sync_news`, `sync_vn_flows`... |
| `target` | varchar(128) | Asset symbol hoặc feed name |
| `status` | enum | `ok`, `partial`, `error` |
| `rows` | int | Số dòng đã upsert |
| `message` | text nullable | Error trace nếu fail |
| `started_at`, `finished_at` | datetime | |

## 1.3 Quy trình

### Sync prices (per asset)

```
asset → fetcher (registry chọn theo source) → DataFrame OHLCV
     → bulk_upsert_prices (ON DUPLICATE KEY UPDATE)
     → if vn_stock: bulk_upsert_vn_flows + bulk_upsert_corporate_events
     → write_fetch_log(rows=n, status=ok|error)
```

- **Idempotent**: chạy lại không tạo duplicate (nhờ PK + ON DUPLICATE KEY UPDATE).
- **Resumable**: nếu 1 asset fail, các asset khác vẫn chạy tiếp (try/except per-asset).
- **Default lookback**: 365 bars cho lần đầu, sau đó từ `latest_price_ts` về present.

### Sync news

```
for each feed in watchlist.news_sources:
    parse RSS → list of news items
    bulk_upsert_news (UNIQUE on link → dedup)
```

### vnstock fallback

`data/vn_flows.py` thử 3 API name khác nhau (`foreign_buy_sell`, `foreign_trade`, `quote.foreign`) vì vnstock 3.x chưa stable — wrap try/except mỗi attempt.

## 1.4 Validation rules

- `assets.symbol` UNIQUE — `upsert_asset` insert nếu chưa có, update meta nếu khác.
- `prices` PK `(asset_id, timeframe, ts)` đảm bảo không duplicate bar.
- `news.link` UNIQUE — RSS có thể trả lại bài cũ.
- `corporate_events`: 1 asset có thể có nhiều event cùng `ex_date` (cash + stock dividend) — không UNIQUE constraint, dùng `(asset_id, event_type, ex_date)` làm soft-key ở repo layer.
- Ngày giao dịch VN nghỉ → `sync-prices` không fail, chỉ `rows=0`.

## 1.5 Edge cases

- **vnstock API timeout**: catch trong fetcher, ghi `fetch_log(status=error)`, sang asset tiếp theo.
- **Symbol delisted**: vnstock trả empty DataFrame → `fetch_log(rows=0)`, không exception.
- **RSS feed down**: skip feed đó, log warning, không block các feed khác.
- **`context_only` asset**: vẫn fetch bình thường để Module 2 dùng làm macro context. Chỉ skip ở signal pipeline.
- **Retroactive corporate event**: vnstock có thể bổ sung event lịch sử → upsert sẽ overwrite, OK.

## 1.6 CLI

```bash
sync-prices [--symbol FPT]   # all primary + context, hoặc 1 symbol
sync-news                    # all feeds
```

## 1.7 Phân quyền (Capacities)

Bot single-user local, không có RBAC. Mọi command được run bởi user owner. Quyền truy cập DB cấp ở MySQL user level (xem `.env`).
