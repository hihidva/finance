// Mirror of src/finance_bot/web/schemas.py — keep in sync when adding fields.

export type AssetClass = 'vn_stock' | 'crypto' | 'commodity' | 'fx_index';
export type SourceName = 'vnstock' | 'ccxt' | 'yfinance';
export type Side = 'buy' | 'sell' | 'hold';
export type Tier = 'A' | 'B' | 'C';
export type UserDecision = 'entered' | 'skipped';

export interface WatchlistEntry {
  id: number;
  symbol: string;
  name: string;
  asset_class: AssetClass;
  source: SourceName;
  exchange: string | null;
  timeframes: string[];
  context_only: boolean;
  is_active: boolean;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface WatchlistEntryCreate {
  symbol: string;
  name: string;
  asset_class: AssetClass;
  source: SourceName;
  exchange?: string | null;
  timeframes?: string[];
  context_only?: boolean;
  is_active?: boolean;
  note?: string | null;
}

export type WatchlistEntryPatch = Partial<WatchlistEntryCreate>;

export interface Candle {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PricesResponse {
  symbol: string;
  asset_class: AssetClass;
  timeframe: string;
  candles: Candle[];
}

export interface IndicatorSeries {
  name: string;
  values: [string, number | null][];
}

export interface IndicatorsResponse {
  symbol: string;
  timeframe: string;
  series: IndicatorSeries[];
  available: string[];
}

export interface SignalListItem {
  id: number;
  asset_id: number;
  symbol: string;
  ts: string;
  side: Side;
  tier: Tier;
  confidence: number;
  price_at_signal: number;
  entry_window: 'immediate' | 'ato_next_session';
  expected_entry_at: string | null;
  stop_loss: number | null;
  take_profit: number | null;
  notified: boolean;
  user_decision: UserDecision | null;
  indicators_summary: string;
  pnl_1d: number | null;
  pnl_3d: number | null;
  pnl_7d: number | null;
  pnl_30d: number | null;
}

export interface SignalListResponse {
  items: SignalListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface OutcomeOut {
  horizon_hours: number;
  evaluated_at: string;
  price_then: number;
  pnl_pct: number;
  hit_target: boolean;
  max_favorable: number | null;
  max_adverse: number | null;
}

export interface SignalDetail extends SignalListItem {
  asset_name: string;
  asset_class: AssetClass;
  indicators: Record<string, unknown>;
  news_context: Record<string, unknown> | null;
  rag_context: Record<string, unknown> | null;
  llm_model: string | null;
  llm_reasoning: string | null;
  notified_at: string | null;
  user_decision_at: string | null;
  outcomes: OutcomeOut[];
}

export interface HealthResponse {
  db: boolean;
  llm: boolean;
  llm_model: string;
  watchlist_count: number;
}

// Micro checklist (Phần 1.1 – 2.4) — emitted by analysis/evaluation_micro.py
// and persisted under signals.indicators.micro_score.checklist_report.
export type CheckStatus = 'pass' | 'fail' | 'n/a';

export interface ChecklistCheck {
  name: string;
  status: CheckStatus;
  value: string;
  threshold: string;
  reason: string;
}

export interface ChecklistSection {
  code: string;          // "1.1" .. "2.4"
  name: string;          // Vietnamese label
  checks: ChecklistCheck[];
  passed: number;
  failed: number;
  n_a: number;
}

export interface ChecklistReport {
  section_1_1: ChecklistSection;
  section_1_2: ChecklistSection;
  section_1_3: ChecklistSection;
  section_2_1: ChecklistSection;
  section_2_2: ChecklistSection;
  section_2_3: ChecklistSection;
  section_2_4: ChecklistSection;
}
