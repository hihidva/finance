'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { Candle, Side, Tier } from '@/lib/types';

export interface IndicatorOverlay {
  id: string;
  label: string;
  color: string;
  points: { ts: string; value: number | null }[];
}

export interface SubpaneSeries {
  id: string;
  values: { ts: string; value: number | null }[];
}

export interface SignalMarker {
  ts: string;
  side: Side;
  tier: Tier;
}

interface Props {
  candles: Candle[];
  overlays: IndicatorOverlay[];
  subpanes: SubpaneSeries[];
  markers: SignalMarker[];
}

export function CandleChart({ candles, overlays, subpanes, markers }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const overlaySeriesRef = useRef<Map<string, ISeriesApi<'Line'>>>(new Map());

  // Main chart init
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#475569',
      },
      grid: {
        vertLines: { color: '#f1f5f9' },
        horzLines: { color: '#f1f5f9' },
      },
      timeScale: { borderColor: '#e2e8f0' },
      rightPriceScale: { borderColor: '#e2e8f0' },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#16a34a',
      downColor: '#dc2626',
      borderUpColor: '#16a34a',
      borderDownColor: '#dc2626',
      wickUpColor: '#16a34a',
      wickDownColor: '#dc2626',
    });

    const volSeries = chart.addHistogramSeries({
      color: '#cbd5e1',
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
    });
    volSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      overlaySeriesRef.current.clear();
    };
  }, []);

  // Candle + volume data
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
    const candleData = candles.map((c) => ({
      time: toUtcTime(c.ts),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    const volData = candles.map((c) => ({
      time: toUtcTime(c.ts),
      value: c.volume,
      color: c.close >= c.open ? '#bbf7d0' : '#fecaca',
    }));
    candleSeriesRef.current.setData(candleData);
    volumeSeriesRef.current.setData(volData);
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  // Markers from signals
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    const sigMarkers: SeriesMarker<Time>[] = markers.map((m) => ({
      time: toUtcTime(m.ts),
      position: m.side === 'buy' ? 'belowBar' : m.side === 'sell' ? 'aboveBar' : 'inBar',
      color:
        m.tier === 'A' ? (m.side === 'buy' ? '#15803d' : m.side === 'sell' ? '#b91c1c' : '#64748b')
        : m.tier === 'B' ? '#ca8a04'
        : '#94a3b8',
      shape: m.side === 'buy' ? 'arrowUp' : m.side === 'sell' ? 'arrowDown' : 'circle',
      text: m.tier,
    }));
    candleSeriesRef.current.setMarkers(sigMarkers);
  }, [markers]);

  // Overlay indicator lines — diff existing series and add/remove/update.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const currentIds = new Set(overlays.map((o) => o.id));
    // Remove series no longer requested
    for (const [id, series] of overlaySeriesRef.current.entries()) {
      if (!currentIds.has(id)) {
        chart.removeSeries(series);
        overlaySeriesRef.current.delete(id);
      }
    }

    // Add/update
    for (const overlay of overlays) {
      let series = overlaySeriesRef.current.get(overlay.id);
      if (!series) {
        series = chart.addLineSeries({
          color: overlay.color,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        overlaySeriesRef.current.set(overlay.id, series);
      }
      const data = overlay.points
        .filter((p) => p.value !== null)
        .map((p) => ({
          time: toUtcTime(p.ts),
          value: p.value as number,
        }));
      series.setData(data);
    }
  }, [overlays]);

  return (
    <div className="space-y-3">
      <div
        ref={containerRef}
        className="w-full h-[480px] bg-white border border-slate-200 rounded-lg"
      />
      {subpanes.map((sp) => (
        <SubPane key={sp.id} title={sp.id} values={sp.values} />
      ))}
    </div>
  );
}

function SubPane({
  title,
  values,
}: {
  title: string;
  values: { ts: string; value: number | null }[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#64748b',
      },
      grid: {
        vertLines: { color: '#f1f5f9' },
        horzLines: { color: '#f1f5f9' },
      },
      timeScale: { borderColor: '#e2e8f0' },
      rightPriceScale: { borderColor: '#e2e8f0' },
    });
    const isHist = title.endsWith('_hist');
    const series = isHist
      ? chart.addHistogramSeries({ color: '#64748b' })
      : chart.addLineSeries({ color: '#2563eb', lineWidth: 2 });

    const data = values
      .filter((p) => p.value !== null)
      .map((p) => ({ time: toUtcTime(p.ts), value: p.value as number }));
    series.setData(data);
    chart.timeScale().fitContent();

    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [title, values]);

  return (
    <div>
      <div className="text-xs text-slate-500 mb-1">{title}</div>
      <div
        ref={containerRef}
        className="w-full h-[140px] bg-white border border-slate-200 rounded-lg"
      />
    </div>
  );
}

function toUtcTime(iso: string): UTCTimestamp {
  return (new Date(iso).getTime() / 1000) as UTCTimestamp;
}
