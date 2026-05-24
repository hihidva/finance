-- finance_bot MySQL schema (MySQL 5.7+, charset utf8mb4)
-- Run: mysql -u root -p < schema.sql

CREATE DATABASE IF NOT EXISTS finance_bot
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE finance_bot;

-- =============================================================
-- assets: catalog of tracked symbols (synced from watchlist.yaml)
-- =============================================================
CREATE TABLE IF NOT EXISTS assets (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    symbol          VARCHAR(32)  NOT NULL,
    name            VARCHAR(128) NOT NULL,
    asset_class     ENUM('vn_stock','crypto','commodity','fx_index') NOT NULL,
    source          VARCHAR(32)  NOT NULL,
    exchange        VARCHAR(32)  DEFAULT NULL,
    context_only    TINYINT(1)   NOT NULL DEFAULT 0,    -- 1 = chỉ làm context vĩ mô, không sinh signal
    is_active       TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_symbol_class (symbol, asset_class)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- prices: OHLCV candles per (asset, timeframe, ts)
-- =============================================================
CREATE TABLE IF NOT EXISTS prices (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id        INT UNSIGNED NOT NULL,
    timeframe       VARCHAR(8)   NOT NULL,        -- 15m, 1h, 4h, 1d
    ts              DATETIME     NOT NULL,        -- candle open time, UTC
    open            DECIMAL(20,8) NOT NULL,
    high            DECIMAL(20,8) NOT NULL,
    low             DECIMAL(20,8) NOT NULL,
    close           DECIMAL(20,8) NOT NULL,
    volume          DECIMAL(28,8) NOT NULL DEFAULT 0,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_asset_tf_ts (asset_id, timeframe, ts),
    KEY idx_asset_tf (asset_id, timeframe, ts DESC),
    CONSTRAINT fk_prices_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- news: collected news items + sentiment + RAG embedding ref
-- =============================================================
CREATE TABLE IF NOT EXISTS news (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source          VARCHAR(64)  NOT NULL,
    url             VARCHAR(768) NOT NULL,
    title           VARCHAR(512) NOT NULL,
    summary         TEXT         DEFAULT NULL,
    published_at    DATETIME     NOT NULL,
    lang            VARCHAR(8)   NOT NULL DEFAULT 'vi',
    tags            JSON         DEFAULT NULL,         -- ["vn_stock","macro_vn"]
    related_symbols JSON         DEFAULT NULL,         -- ["FPT","HPG"]
    sentiment       DECIMAL(4,3) DEFAULT NULL,         -- -1.000 .. 1.000
    sentiment_label ENUM('bullish','bearish','neutral','mixed') DEFAULT NULL,
    chroma_id       VARCHAR(64)  DEFAULT NULL,         -- pointer to ChromaDB doc
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_url (url),
    KEY idx_published (published_at DESC),
    KEY idx_sentiment (sentiment_label, published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- signals: every alert decision (whether sent or filtered)
-- =============================================================
CREATE TABLE IF NOT EXISTS signals (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id        INT UNSIGNED NOT NULL,
    timeframe       VARCHAR(8)   NOT NULL,
    ts              DATETIME     NOT NULL,             -- candle ts that triggered
    side            ENUM('buy','sell','hold') NOT NULL,
    tier            ENUM('A','B','C') NOT NULL DEFAULT 'C',
    confidence      DECIMAL(4,3) NOT NULL,             -- 0.000 .. 1.000
    price_at_signal DECIMAL(20,8) NOT NULL,
    entry_window    ENUM('immediate','ato_next_session') NOT NULL DEFAULT 'immediate',
    expected_entry_at DATETIME    DEFAULT NULL,        -- ước tính khi user khớp lệnh (UTC)
    stop_loss       DECIMAL(20,8) DEFAULT NULL,
    take_profit     DECIMAL(20,8) DEFAULT NULL,
    indicators      JSON         NOT NULL,             -- {rsi:.., macd:.., ema_cross:..}
    news_context    JSON         DEFAULT NULL,         -- top-k news ids + sentiment
    rag_context     JSON         DEFAULT NULL,         -- past similar cases retrieved
    llm_model       VARCHAR(64)  DEFAULT NULL,
    llm_reasoning   TEXT         DEFAULT NULL,
    notified        TINYINT(1)   NOT NULL DEFAULT 0,
    notified_at     DATETIME     DEFAULT NULL,
    notification_message_id BIGINT DEFAULT NULL,       -- Telegram message_id để edit reply_markup sau ack
    user_decision   ENUM('entered','skipped') DEFAULT NULL,  -- user click Telegram button
    user_decision_at DATETIME    DEFAULT NULL,
    chroma_id       VARCHAR(64)  DEFAULT NULL,         -- embedding stored for future RAG
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_asset_ts (asset_id, ts DESC),
    KEY idx_side_conf (side, confidence DESC),
    KEY idx_notified (notified, created_at),
    KEY idx_user_decision (user_decision, created_at),
    CONSTRAINT fk_signals_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- outcomes: post-hoc evaluation of each signal -> RAG feedback
-- horizon_hours: 24, 72, 168 (1d / 3d / 7d)
-- =============================================================
CREATE TABLE IF NOT EXISTS outcomes (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    signal_id       BIGINT UNSIGNED NOT NULL,
    horizon_hours   SMALLINT UNSIGNED NOT NULL,
    evaluated_at    DATETIME     NOT NULL,
    price_then      DECIMAL(20,8) NOT NULL,            -- price horizon hours after signal
    pnl_pct         DECIMAL(8,4) NOT NULL,             -- assuming side direction
    hit_target      TINYINT(1)   NOT NULL DEFAULT 0,   -- did move follow signal direction
    max_favorable   DECIMAL(8,4) DEFAULT NULL,         -- best % move during horizon
    max_adverse     DECIMAL(8,4) DEFAULT NULL,         -- worst % move during horizon
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_signal_horizon (signal_id, horizon_hours),
    KEY idx_hit (hit_target, horizon_hours),
    CONSTRAINT fk_outcomes_signal FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- vn_flows: VN-stock-specific daily flows (foreign / proprietary / margin)
-- Một dòng = 1 ngày × 1 mã. Đơn vị: VND khối lượng.
-- =============================================================
CREATE TABLE IF NOT EXISTS vn_flows (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id        INT UNSIGNED NOT NULL,
    trade_date      DATE         NOT NULL,
    foreign_buy_vol     DECIMAL(28,8) DEFAULT NULL,
    foreign_sell_vol    DECIMAL(28,8) DEFAULT NULL,
    foreign_net_vol     DECIMAL(28,8) DEFAULT NULL,
    foreign_buy_value   DECIMAL(28,8) DEFAULT NULL,    -- VND
    foreign_sell_value  DECIMAL(28,8) DEFAULT NULL,
    foreign_net_value   DECIMAL(28,8) DEFAULT NULL,
    proprietary_net_vol     DECIMAL(28,8) DEFAULT NULL, -- tự doanh
    proprietary_net_value   DECIMAL(28,8) DEFAULT NULL,
    margin_outstanding      DECIMAL(28,8) DEFAULT NULL, -- dư nợ margin theo mã (nếu lấy được)
    raw_payload     JSON         DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_asset_date (asset_id, trade_date),
    KEY idx_date (trade_date),
    CONSTRAINT fk_vn_flows_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- corporate_events: ngày GDKHQ, chia cổ tức, phát hành thêm, ...
-- =============================================================
CREATE TABLE IF NOT EXISTS corporate_events (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id        INT UNSIGNED NOT NULL,
    event_type      ENUM(
        'ex_rights',       -- ngày giao dịch không hưởng quyền
        'cash_dividend',
        'stock_dividend',
        'rights_issue',    -- phát hành thêm cho cổ đông hiện hữu
        'stock_split',
        'agm',             -- đại hội cổ đông
        'other'
    ) NOT NULL,
    event_date      DATE         NOT NULL,
    record_date     DATE         DEFAULT NULL,
    payment_date    DATE         DEFAULT NULL,
    ratio           VARCHAR(64)  DEFAULT NULL,         -- "10:1", "5%", ...
    cash_amount     DECIMAL(20,4) DEFAULT NULL,
    description     TEXT         DEFAULT NULL,
    raw_payload     JSON         DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_asset_event (asset_id, event_type, event_date),
    KEY idx_event_date (event_date),
    CONSTRAINT fk_events_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- knowledge: user-fed kiến thức để LLM "học thêm" qua RAG
-- (vd: "DXY > 105 thường ép giá vàng", "MBB hay tăng cuối quý"...)
-- =============================================================
CREATE TABLE IF NOT EXISTS knowledge (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    title           VARCHAR(255) NOT NULL,
    body            TEXT         NOT NULL,
    tags            JSON         DEFAULT NULL,         -- ["xau","dxy","macro"]
    source          ENUM('user','auto','external') NOT NULL DEFAULT 'user',
    chroma_id       VARCHAR(64)  DEFAULT NULL,         -- embedding id trong ChromaDB
    is_active       TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_active (is_active, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- fetch_log: track each fetch run for debugging & gap detection
-- =============================================================
CREATE TABLE IF NOT EXISTS fetch_log (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id        INT UNSIGNED DEFAULT NULL,
    source          VARCHAR(32)  NOT NULL,
    kind            ENUM('price','news','vn_flow','corp_event') NOT NULL,
    timeframe       VARCHAR(8)   DEFAULT NULL,
    started_at      DATETIME     NOT NULL,
    finished_at     DATETIME     DEFAULT NULL,
    status          ENUM('ok','partial','error') NOT NULL,
    rows_inserted   INT UNSIGNED DEFAULT 0,
    error_message   TEXT         DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_asset_source (asset_id, source, started_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
