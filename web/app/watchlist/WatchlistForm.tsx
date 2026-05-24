'use client';

import { useState, type FormEvent } from 'react';
import { api, ApiError } from '@/lib/api';
import type {
  AssetClass,
  SourceName,
  WatchlistEntry,
  WatchlistEntryCreate,
} from '@/lib/types';

const ASSET_CLASS_OPTIONS: AssetClass[] = ['vn_stock', 'crypto', 'commodity', 'fx_index'];

const SOURCE_BY_CLASS: Record<AssetClass, SourceName[]> = {
  vn_stock: ['vnstock'],
  crypto: ['ccxt'],
  commodity: ['yfinance'],
  fx_index: ['yfinance'],
};

interface Props {
  mode: 'create' | 'edit';
  initial?: WatchlistEntry;
  onSaved: () => void;
}

export function WatchlistForm({ mode, initial, onSaved }: Props) {
  const [symbol, setSymbol] = useState(initial?.symbol ?? '');
  const [name, setName] = useState(initial?.name ?? '');
  const [assetClass, setAssetClass] = useState<AssetClass>(
    initial?.asset_class ?? 'vn_stock',
  );
  const [source, setSource] = useState<SourceName>(
    initial?.source ?? SOURCE_BY_CLASS[initial?.asset_class ?? 'vn_stock'][0],
  );
  const [exchange, setExchange] = useState(initial?.exchange ?? '');
  const [contextOnly, setContextOnly] = useState(initial?.context_only ?? false);
  const [isActive, setIsActive] = useState(initial?.is_active ?? true);
  const [note, setNote] = useState(initial?.note ?? '');

  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const payload: WatchlistEntryCreate = {
        symbol: symbol.trim(),
        name: name.trim() || symbol.trim(),
        asset_class: assetClass,
        source,
        exchange: exchange.trim() || null,
        timeframes: initial?.timeframes ?? ['1d'],
        context_only: contextOnly,
        is_active: isActive,
        note: note.trim() || null,
      };

      if (mode === 'create') {
        await api('/api/watchlist', { method: 'POST', body: JSON.stringify(payload) });
      } else if (initial) {
        await api(`/api/watchlist/${initial.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  function onAssetClassChange(next: AssetClass) {
    setAssetClass(next);
    setSource(SOURCE_BY_CLASS[next][0]);
  }

  return (
    <form onSubmit={submit} className="space-y-4 text-sm">
      <Field label="Symbol" required>
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          required
          disabled={mode === 'edit'}
          pattern="^[A-Z0-9./-]{1,32}$"
          className="w-full rounded border border-slate-300 px-2 py-1.5 disabled:bg-slate-100"
        />
      </Field>

      <Field label="Tên hiển thị">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded border border-slate-300 px-2 py-1.5"
        />
      </Field>

      <Field label="Asset class" required>
        <select
          value={assetClass}
          onChange={(e) => onAssetClassChange(e.target.value as AssetClass)}
          className="w-full rounded border border-slate-300 px-2 py-1.5"
        >
          {ASSET_CLASS_OPTIONS.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </Field>

      <Field label="Source" required>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value as SourceName)}
          className="w-full rounded border border-slate-300 px-2 py-1.5"
        >
          {SOURCE_BY_CLASS[assetClass].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </Field>

      {assetClass === 'vn_stock' && (
        <Field label="Exchange">
          <input
            value={exchange}
            onChange={(e) => setExchange(e.target.value.toUpperCase())}
            placeholder="HOSE / HNX / UPCOM"
            className="w-full rounded border border-slate-300 px-2 py-1.5"
          />
        </Field>
      )}

      <Field label="Ghi chú">
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          className="w-full rounded border border-slate-300 px-2 py-1.5"
        />
      </Field>

      <div className="flex items-center gap-6">
        <label className="inline-flex items-center gap-2">
          <input
            type="checkbox"
            checked={contextOnly}
            onChange={(e) => setContextOnly(e.target.checked)}
          />
          <span>Context only (chỉ feed LLM, không sinh signal)</span>
        </label>
        <label className="inline-flex items-center gap-2">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
          <span>Active</span>
        </label>
      </div>

      {error && (
        <div className="rounded border border-rose-200 bg-rose-50 p-2 text-rose-700">
          {error}
        </div>
      )}

      <div className="flex justify-end pt-2">
        <button
          type="submit"
          disabled={submitting}
          className="px-4 py-1.5 rounded bg-slate-900 text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {submitting ? 'Đang lưu…' : 'Lưu'}
        </button>
      </div>
    </form>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-slate-700">
        {label} {required && <span className="text-rose-600">*</span>}
      </span>
      {children}
    </label>
  );
}
