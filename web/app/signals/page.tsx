'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import useSWR, { mutate } from 'swr';
import clsx from 'clsx';
import { api, swrFetcher } from '@/lib/api';
import { fmtDateTime, fmtPrice, fmtPct, pctColor } from '@/lib/format';
import type {
  SignalDetail,
  SignalListItem,
  SignalListResponse,
  Tier,
  Side,
  UserDecision,
  WatchlistEntry,
} from '@/lib/types';
import { DecisionBadge, SideText, TierBadge } from '@/components/Badges';
import { Drawer } from '@/components/Drawer';
import { ChecklistPanel, extractChecklistReport } from '@/components/ChecklistPanel';

const ALL_TIERS: Tier[] = ['A', 'B', 'C'];
const ALL_SIDES: Side[] = ['buy', 'sell', 'hold'];

type DecisionFilter = 'all' | 'entered' | 'skipped' | 'pending';

export default function SignalsPage() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [tiers, setTiers] = useState<Tier[]>(['A', 'B']);
  const [sides, setSides] = useState<Side[]>(['buy', 'sell']);
  const [decision, setDecision] = useState<DecisionFilter>('all');
  const [notifiedOnly, setNotifiedOnly] = useState(false);
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [openId, setOpenId] = useState<number | null>(null);

  const { data: watchlist } = useSWR<WatchlistEntry[]>(
    '/api/watchlist?only_active=true',
    swrFetcher,
  );

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (symbols.length) params.set('symbols', symbols.join(','));
    if (tiers.length) params.set('tiers', tiers.join(','));
    if (sides.length) params.set('sides', sides.join(','));
    if (decision !== 'all') params.set('user_decision', decision);
    if (notifiedOnly) params.set('notified', 'true');
    params.set('page', String(page));
    params.set('page_size', String(pageSize));
    return params.toString();
  }, [symbols, tiers, sides, decision, notifiedOnly, page]);

  const { data, error, isLoading } = useSWR<SignalListResponse>(
    `/api/signals?${query}`,
    swrFetcher,
  );

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Lịch sử signals</h1>
        <p className="text-sm text-slate-600">
          Tier B/C cũng hiển thị (không alert nhưng lưu cho training RAG).
        </p>
      </header>

      <section className="bg-white border border-slate-200 rounded-lg p-3 space-y-3">
        <div className="flex flex-wrap gap-x-6 gap-y-3 text-sm items-center">
          <FilterGroup label="Tier">
            {ALL_TIERS.map((t) => (
              <ChipToggle
                key={t}
                label={t}
                active={tiers.includes(t)}
                onToggle={() =>
                  setTiers((prev) =>
                    prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
                  )
                }
              />
            ))}
          </FilterGroup>

          <FilterGroup label="Side">
            {ALL_SIDES.map((s) => (
              <ChipToggle
                key={s}
                label={s}
                active={sides.includes(s)}
                onToggle={() =>
                  setSides((prev) =>
                    prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
                  )
                }
              />
            ))}
          </FilterGroup>

          <FilterGroup label="Decision">
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value as DecisionFilter)}
              className="text-xs rounded border border-slate-300 px-2 py-1"
            >
              <option value="all">Tất cả</option>
              <option value="entered">Đã vào lệnh</option>
              <option value="skipped">Bỏ qua</option>
              <option value="pending">Chưa phản hồi</option>
            </select>
          </FilterGroup>

          <label className="inline-flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={notifiedOnly}
              onChange={(e) => setNotifiedOnly(e.target.checked)}
            />
            <span>Chỉ những signal đã alert Telegram</span>
          </label>
        </div>

        <FilterGroup label="Symbols">
          <div className="flex flex-wrap gap-1">
            {watchlist?.map((w) => (
              <ChipToggle
                key={w.id}
                label={w.symbol}
                active={symbols.includes(w.symbol)}
                onToggle={() =>
                  setSymbols((prev) =>
                    prev.includes(w.symbol)
                      ? prev.filter((x) => x !== w.symbol)
                      : [...prev, w.symbol],
                  )
                }
              />
            ))}
            {symbols.length > 0 && (
              <button
                type="button"
                onClick={() => setSymbols([])}
                className="text-xs text-slate-500 hover:underline ml-2"
              >
                Bỏ chọn tất cả
              </button>
            )}
          </div>
        </FilterGroup>
      </section>

      {error && (
        <div className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {(error as Error).message}
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-lg overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-left">
            <tr>
              <th className="px-3 py-2 font-medium">Thời gian</th>
              <th className="px-3 py-2 font-medium">Symbol</th>
              <th className="px-3 py-2 font-medium">Tier</th>
              <th className="px-3 py-2 font-medium">Side</th>
              <th className="px-3 py-2 font-medium text-right">Conf</th>
              <th className="px-3 py-2 font-medium">Indicators</th>
              <th className="px-3 py-2 font-medium text-right">Entry</th>
              <th className="px-3 py-2 font-medium text-right">SL/TP</th>
              <th className="px-3 py-2 font-medium text-right">1d</th>
              <th className="px-3 py-2 font-medium text-right">3d</th>
              <th className="px-3 py-2 font-medium text-right">7d</th>
              <th className="px-3 py-2 font-medium text-right">30d</th>
              <th className="px-3 py-2 font-medium">Decision</th>
              <th className="px-3 py-2 font-medium text-center">Notified</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr><td colSpan={15} className="px-3 py-6 text-center text-slate-500">Đang tải…</td></tr>
            )}
            {!isLoading && data?.items.length === 0 && (
              <tr><td colSpan={15} className="px-3 py-6 text-center text-slate-500">
                Không có signal nào khớp filter.
              </td></tr>
            )}
            {data?.items.map((s) => <Row key={s.id} signal={s} onOpen={() => setOpenId(s.id)} />)}
          </tbody>
        </table>
      </div>

      <Pagination
        page={page}
        pageSize={pageSize}
        total={data?.total ?? 0}
        onChange={setPage}
      />

      <Drawer
        open={openId !== null}
        onClose={() => setOpenId(null)}
        title={`Signal #${openId ?? ''}`}
        width="max-w-2xl"
      >
        {openId !== null && <SignalDetailView id={openId} onMutated={() => mutate((key) => typeof key === 'string' && key.startsWith('/api/signals'))} />}
      </Drawer>
    </div>
  );
}

function Row({ signal, onOpen }: { signal: SignalListItem; onOpen: () => void }) {
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-3 py-2 text-slate-700 whitespace-nowrap">{fmtDateTime(signal.ts)}</td>
      <td className="px-3 py-2 font-semibold">
        <Link
          href={`/charts/${encodeURIComponent(signal.symbol)}`}
          className="text-slate-900 hover:underline"
        >
          {signal.symbol}
        </Link>
      </td>
      <td className="px-3 py-2"><TierBadge value={signal.tier} /></td>
      <td className="px-3 py-2"><SideText value={signal.side} /></td>
      <td className="px-3 py-2 text-right tabular">{signal.confidence.toFixed(2)}</td>
      <td className="px-3 py-2 text-slate-600 text-xs">{signal.indicators_summary}</td>
      <td className="px-3 py-2 text-right tabular">{fmtPrice(signal.price_at_signal)}</td>
      <td className="px-3 py-2 text-right tabular text-xs text-slate-500">
        {signal.stop_loss != null ? fmtPrice(signal.stop_loss) : '—'}
        <br />
        {signal.take_profit != null ? fmtPrice(signal.take_profit) : '—'}
      </td>
      <PnlCell value={signal.pnl_1d} />
      <PnlCell value={signal.pnl_3d} />
      <PnlCell value={signal.pnl_7d} />
      <PnlCell value={signal.pnl_30d} />
      <td className="px-3 py-2"><DecisionBadge value={signal.user_decision} /></td>
      <td className="px-3 py-2 text-center">{signal.notified ? '✅' : '—'}</td>
      <td className="px-3 py-2 text-right">
        <button
          type="button"
          onClick={onOpen}
          className="text-xs text-slate-700 hover:underline"
        >
          View
        </button>
      </td>
    </tr>
  );
}

function PnlCell({ value }: { value: number | null }) {
  return (
    <td className={clsx('px-3 py-2 text-right tabular text-xs', pctColor(value))}>
      {fmtPct(value)}
    </td>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-slate-500 mr-1">{label}:</span>
      <div className="flex items-center gap-1">{children}</div>
    </div>
  );
}

function ChipToggle({
  label,
  active,
  onToggle,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={clsx(
        'px-2 py-0.5 rounded text-xs border',
        active
          ? 'bg-slate-900 text-white border-slate-900'
          : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50',
      )}
    >
      {label}
    </button>
  );
}

function Pagination({
  page,
  pageSize,
  total,
  onChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onChange: (p: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between text-sm text-slate-600">
      <span>
        {total === 0 ? 0 : (page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} / {total}
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
          className="px-2 py-1 rounded border border-slate-300 disabled:opacity-40"
        >
          ← Prev
        </button>
        <span className="px-2 py-1">{page} / {totalPages}</span>
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => onChange(page + 1)}
          className="px-2 py-1 rounded border border-slate-300 disabled:opacity-40"
        >
          Next →
        </button>
      </div>
    </div>
  );
}

function SignalDetailView({ id, onMutated }: { id: number; onMutated: () => void }) {
  const { data, error, isLoading } = useSWR<SignalDetail>(
    `/api/signals/${id}`,
    swrFetcher,
  );
  const [busy, setBusy] = useState(false);

  async function setDecision(decision: UserDecision | null) {
    setBusy(true);
    try {
      await api(`/api/signals/${id}/user-decision`, {
        method: 'PATCH',
        body: JSON.stringify({ decision }),
      });
      await mutate(`/api/signals/${id}`);
      onMutated();
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <p className="text-slate-500">Đang tải…</p>;
  if (error) return <p className="text-rose-600 text-sm">{(error as Error).message}</p>;
  if (!data) return null;

  return (
    <div className="space-y-4 text-sm">
      <header className="flex items-center justify-between">
        <div>
          <div className="text-lg font-semibold">
            {data.symbol} <span className="text-slate-400">— {data.asset_name}</span>
          </div>
          <div className="text-slate-500">
            {fmtDateTime(data.ts)} • <SideText value={data.side} /> • Tier {data.tier}
          </div>
        </div>
        <TierBadge value={data.tier} />
      </header>

      <section className="grid grid-cols-2 gap-3">
        <Stat label="Confidence" value={data.confidence.toFixed(2)} />
        <Stat label="Entry window" value={data.entry_window} />
        <Stat label="Price at signal" value={fmtPrice(data.price_at_signal)} />
        <Stat label="Expected entry" value={fmtDateTime(data.expected_entry_at)} />
        <Stat label="Stop loss" value={fmtPrice(data.stop_loss)} />
        <Stat label="Take profit" value={fmtPrice(data.take_profit)} />
      </section>

      {data.llm_reasoning && (
        <section>
          <h3 className="font-medium text-slate-700 mb-1">LLM reasoning</h3>
          <p className="bg-slate-50 rounded p-2 whitespace-pre-wrap text-slate-700">
            {data.llm_reasoning}
          </p>
          {data.llm_model && (
            <p className="text-xs text-slate-400 mt-1">Model: {data.llm_model}</p>
          )}
        </section>
      )}

      <section data-testid="checklist-vi-mo">
        <h3 className="font-medium text-slate-700 mb-1">Checklist vi mô</h3>
        {extractChecklistReport(data.indicators) ? (
          <ChecklistPanel report={extractChecklistReport(data.indicators)} />
        ) : (
          <p className="text-xs text-slate-500">
            Signal cũ (trước khi rollout v2) hoặc chưa có dữ liệu fundamentals.
          </p>
        )}
      </section>

      <section>
        <h3 className="font-medium text-slate-700 mb-1">Outcomes</h3>
        {data.outcomes.length === 0 ? (
          <p className="text-slate-500">Chưa có outcome (signal chưa đủ horizon).</p>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-slate-500 text-left">
              <tr><th>Horizon</th><th>Eval at</th><th className="text-right">PnL</th><th className="text-right">Best</th><th className="text-right">Worst</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.outcomes.map((o) => (
                <tr key={o.horizon_hours}>
                  <td>{o.horizon_hours}h</td>
                  <td className="text-slate-500">{fmtDateTime(o.evaluated_at)}</td>
                  <td className={clsx('text-right tabular', pctColor(o.pnl_pct))}>{fmtPct(o.pnl_pct)}</td>
                  <td className={clsx('text-right tabular', pctColor(o.max_favorable))}>{fmtPct(o.max_favorable)}</td>
                  <td className={clsx('text-right tabular', pctColor(o.max_adverse))}>{fmtPct(o.max_adverse)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h3 className="font-medium text-slate-700 mb-1">User decision</h3>
        <div className="flex items-center gap-2">
          <DecisionBadge value={data.user_decision} />
          <button
            type="button"
            disabled={busy}
            onClick={() => setDecision('entered')}
            className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-slate-50"
          >
            Mark entered
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setDecision('skipped')}
            className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-slate-50"
          >
            Mark skipped
          </button>
          {data.user_decision && (
            <button
              type="button"
              disabled={busy}
              onClick={() => setDecision(null)}
              className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-slate-50 text-rose-700"
            >
              Clear
            </button>
          )}
        </div>
      </section>

      <details className="text-xs">
        <summary className="cursor-pointer text-slate-500">Raw JSON</summary>
        <pre className="bg-slate-50 rounded p-2 overflow-x-auto">
          {JSON.stringify({
            indicators: data.indicators,
            news_context: data.news_context,
            rag_context: data.rag_context,
          }, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="bg-slate-50 rounded p-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-sm text-slate-800 tabular">{value ?? '—'}</div>
    </div>
  );
}
