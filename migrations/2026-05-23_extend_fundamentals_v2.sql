-- =============================================================
-- Migration: extend `fundamental_snapshots` + `industry_averages`
-- for the v2 micro-evaluation engine (checklist 8 sections).
--
-- Two ways to run:
--   1) Via runner (recommended): `./run.sh migrate`  or  `uv run python main.py db-migrate`
--      Runner picks the target DB from .env DATABASE_URL and skips `USE`.
--   2) Manually via CLI (legacy):
--        mysql -u root -p <your-db> < migrations/2026-05-23_extend_fundamentals_v2.sql
--      The `USE` line below is for this manual path; it's a no-op when run
--      via the Python runner (filtered out by db/migrations.py).
-- =============================================================

USE finance_bot;

-- -------------------------------------------------------------
-- fundamental_snapshots: 22 new nullable columns
-- -------------------------------------------------------------
ALTER TABLE fundamental_snapshots
    -- Phần 1.1 — Income statement
    ADD COLUMN revenue              DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN gross_profit         DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN net_profit           DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN eps                  DECIMAL(20,4) DEFAULT NULL,
    -- Phần 1.2 — Balance sheet
    ADD COLUMN cash_and_equivalents DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN total_assets         DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN total_debt           DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN total_equity         DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN inventory            DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN receivables          DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN current_assets       DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN current_liabilities  DECIMAL(20,4) DEFAULT NULL,
    -- Phần 1.3 — Cash flow
    ADD COLUMN cfo                  DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN capex                DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN cff                  DECIMAL(20,4) DEFAULT NULL,
    ADD COLUMN fcf                  DECIMAL(20,4) DEFAULT NULL,
    -- Phần 2.1 — Valuation
    ADD COLUMN ev_ebitda            DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN ps                   DECIMAL(10,4) DEFAULT NULL,
    -- Phần 2.2 — Profitability
    ADD COLUMN roic                 DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN gross_margin         DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN net_margin           DECIMAL(10,4) DEFAULT NULL,
    -- Phần 2.3 — Leverage & liquidity
    ADD COLUMN de_ratio             DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN current_ratio        DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN quick_ratio          DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN interest_coverage    DECIMAL(10,4) DEFAULT NULL,
    -- Phần 2.4 — Operating efficiency
    ADD COLUMN inventory_days       DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN receivable_days      DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN ccc                  DECIMAL(10,4) DEFAULT NULL;

-- Index period_end DESC so v2 picks the latest snapshot per symbol fast.
ALTER TABLE fundamental_snapshots
    ADD KEY idx_symbol_period_end (asset_symbol, period_end DESC);

-- -------------------------------------------------------------
-- industry_averages: 12 new nullable columns (6 ratios × avg/median)
-- -------------------------------------------------------------
ALTER TABLE industry_averages
    ADD COLUMN ev_ebitda_avg        DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN ev_ebitda_median     DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN ps_avg               DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN ps_median            DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN roic_avg             DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN roic_median          DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN gross_margin_avg     DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN gross_margin_median  DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN net_margin_avg       DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN net_margin_median    DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN de_ratio_avg         DECIMAL(10,4) DEFAULT NULL,
    ADD COLUMN de_ratio_median      DECIMAL(10,4) DEFAULT NULL;
