'use client';

import useSWR from 'swr';
import Link from 'next/link';
import { swrFetcher } from '@/lib/api';
import type { HealthResponse } from '@/lib/types';

export default function HomePage() {
  const { data, error, isLoading } = useSWR<HealthResponse>(
    '/api/health',
    swrFetcher,
    { refreshInterval: 30_000 },
  );

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
        <p className="text-slate-600 mt-1">
          Quản lý watchlist, theo dõi đồ thị và tra cứu lịch sử signals của finance-bot.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatusCard
          title="Database"
          ok={data?.db ?? null}
          loading={isLoading || !!error}
          detail="MySQL"
        />
        <StatusCard
          title="Claude CLI"
          ok={data?.llm ?? null}
          loading={isLoading || !!error}
          detail={data?.llm_model ?? '—'}
        />
        <StatusCard
          title="Watchlist"
          ok={data ? data.watchlist_count > 0 : null}
          loading={isLoading || !!error}
          detail={data ? `${data.watchlist_count} active entries` : '—'}
        />
      </section>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <NavCard
          href="/watchlist"
          title="Cấu hình watchlist"
          body="CRUD mã theo dõi, pause/active, export YAML."
        />
        <NavCard
          href="/charts"
          title="Đồ thị giá + chỉ báo"
          body="Candlestick + EMA / Bollinger / RSI / MACD / ATR."
        />
        <NavCard
          href="/signals"
          title="Lịch sử signals"
          body="Filter theo tier, side, decision; xem chi tiết + outcomes."
        />
      </section>
    </div>
  );
}

function StatusCard({
  title,
  ok,
  loading,
  detail,
}: {
  title: string;
  ok: boolean | null;
  loading: boolean;
  detail: string;
}) {
  const color =
    loading || ok === null ? 'bg-slate-100 text-slate-500'
    : ok ? 'bg-emerald-100 text-emerald-700'
    : 'bg-rose-100 text-rose-700';
  const label =
    loading || ok === null ? '…' : ok ? 'OK' : 'FAILED';

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-600">{title}</span>
        <span className={`text-xs px-2 py-0.5 rounded-full ${color}`}>{label}</span>
      </div>
      <div className="mt-2 text-sm text-slate-700">{detail}</div>
    </div>
  );
}

function NavCard({ href, title, body }: { href: string; title: string; body: string }) {
  return (
    <Link
      href={href}
      className="block rounded-lg border border-slate-200 bg-white p-4 hover:shadow-sm hover:border-slate-300"
    >
      <div className="font-medium text-slate-900">{title}</div>
      <div className="text-sm text-slate-600 mt-1">{body}</div>
    </Link>
  );
}
