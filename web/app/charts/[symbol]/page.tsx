'use client';

import { useMemo, useState } from 'react';
import useSWR from 'swr';
import { swrFetcher } from '@/lib/api';
import { fmtDate, fmtPrice, fmtPct, pctColor } from '@/lib/format';
import type { IndicatorsResponse, PricesResponse, SignalListResponse } from '@/lib/types';
import { CandleChart, type IndicatorOverlay, type SignalMarker } from './CandleChart';

type Lookback = 90 | 180 | 365 | 730;
const LOOKBACK_LABELS: Record<Lookback, string> = {
  90: '3M',
  180: '6M',
  365: '1Y',
  730: '2Y',
};

const OVERLAY_TOGGLES = [
  { id: 'ema20', label: 'EMA20', color: '#2563eb' },
  { id: 'ema50', label: 'EMA50', color: '#9333ea' },
  { id: 'ema200', label: 'EMA200', color: '#0f172a' },
  { id: 'bb_upper', label: 'BB upper', color: '#94a3b8' },
  { id: 'bb_lower', label: 'BB lower', color: '#94a3b8' },
];

const SUBPANE_TOGGLES = [
  { id: 'rsi14', label: 'RSI14' },
  { id: 'macd_hist', label: 'MACD hist' },
];

// Next.js 14: `params` is a plain object (Next 15+ would wrap it in a Promise).
export default function ChartSymbolPage({
  params,
}: {
  params: { symbol: string };
}) {
  const symbol = decodeURIComponent(params.symbol);

  const [lookback, setLookback] = useState<Lookback>(180);
  const [overlayIds, setOverlayIds] = useState<string[]>(['ema20', 'ema50']);
  const [subpaneIds, setSubpaneIds] = useState<string[]>([]);

  const { data: prices, isLoading: priceLoading, error: priceError } =
    useSWR<PricesResponse>(
      `/api/prices/${encodeURIComponent(symbol)}?lookback=${lookback}`,
      swrFetcher,
    );

  const indicatorNames = [...overlayIds, ...subpaneIds].join(',');
  const { data: indicators } = useSWR<IndicatorsResponse>(
    indicatorNames
      ? `/api/indicators/${encodeURIComponent(symbol)}?lookback=${lookback}&names=${indicatorNames}`
      : null,
    swrFetcher,
  );

  // Signals as markers — only those within the lookback window.
  const { data: signalsResp } = useSWR<SignalListResponse>(
    `/api/signals?symbols=${encodeURIComponent(symbol)}&page_size=200`,
    swrFetcher,
  );

  const overlays = useMemo<IndicatorOverlay[]>(() => {
    if (!indicators) return [];
    return overlayIds
      .map((id) => {
        const meta = OVERLAY_TOGGLES.find((t) => t.id === id);
        const series = indicators.series.find((s) => s.name === id);
        if (!meta || !series) return null;
        return {
          id: meta.id,
          label: meta.label,
          color: meta.color,
          points: series.values.map(([ts, v]) => ({ ts, value: v })),
        };
      })
      .filter((x): x is IndicatorOverlay => x !== null);
  }, [indicators, overlayIds]);

  const subpaneSeries = useMemo(() => {
    if (!indicators) return [];
    return subpaneIds
      .map((id) => {
        const series = indicators.series.find((s) => s.name === id);
        if (!series) return null;
        return {
          id,
          values: series.values.map(([ts, v]) => ({ ts, value: v })),
        };
      })
      .filter((x): x is { id: string; values: { ts: string; value: number | null }[] } => x !== null);
  }, [indicators, subpaneIds]);

  const markers = useMemo<SignalMarker[]>(() => {
    if (!signalsResp) return [];
    return signalsResp.items.map((s) => ({
      ts: s.ts,
      side: s.side,
      tier: s.tier,
    }));
  }, [signalsResp]);

  const last = prices?.candles.at(-1);
  const prev = prices?.candles.at(-2);
  const changePct =
    last && prev ? ((last.close - prev.close) / prev.close) * 100 : null;

  return (
    <div className="space-y-4">
      <header className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">{symbol}</h1>
          <p className="text-sm text-slate-500">
            {prices ? `${prices.asset_class} • ${prices.timeframe}` : '…'}
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular">
            {last ? fmtPrice(last.close) : '—'}
          </div>
          <div className={`text-sm tabular ${pctColor(changePct)}`}>
            {fmtPct(changePct)} {last ? `• ${fmtDate(last.ts)}` : ''}
          </div>
        </div>
      </header>

      <section className="flex items-center justify-between gap-3 flex-wrap text-sm">
        <div className="flex items-center gap-1">
          <span className="text-slate-500 mr-2">Lookback:</span>
          {(Object.keys(LOOKBACK_LABELS) as unknown as Lookback[]).map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setLookback(Number(d) as Lookback)}
              className={`px-2 py-1 rounded text-xs border ${
                Number(d) === lookback
                  ? 'bg-slate-900 text-white border-slate-900'
                  : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
              }`}
            >
              {LOOKBACK_LABELS[d as Lookback]}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-slate-500">Overlay:</span>
          {OVERLAY_TOGGLES.map((t) => (
            <Toggle
              key={t.id}
              label={t.label}
              checked={overlayIds.includes(t.id)}
              onChange={(checked) =>
                setOverlayIds((prev) =>
                  checked ? [...prev, t.id] : prev.filter((x) => x !== t.id),
                )
              }
            />
          ))}
          <span className="text-slate-500 ml-3">Sub-pane:</span>
          {SUBPANE_TOGGLES.map((t) => (
            <Toggle
              key={t.id}
              label={t.label}
              checked={subpaneIds.includes(t.id)}
              onChange={(checked) =>
                setSubpaneIds((prev) =>
                  checked ? [...prev, t.id] : prev.filter((x) => x !== t.id),
                )
              }
            />
          ))}
        </div>
      </section>

      {priceError && (
        <div className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          Không tải được dữ liệu giá: {(priceError as Error).message}
        </div>
      )}
      {priceLoading && (
        <div className="text-slate-500 text-sm">Đang tải dữ liệu…</div>
      )}

      {prices && (
        <CandleChart
          candles={prices.candles}
          overlays={overlays}
          subpanes={subpaneSeries}
          markers={markers}
        />
      )}
    </div>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="inline-flex items-center gap-1 text-xs cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="rounded"
      />
      <span className="text-slate-700">{label}</span>
    </label>
  );
}
