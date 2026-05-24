'use client';

import { useState } from 'react';
import Link from 'next/link';
import useSWR, { mutate } from 'swr';
import { api, ApiError, swrFetcher } from '@/lib/api';
import type { WatchlistEntry } from '@/lib/types';
import { ActiveBadge, AssetClassBadge } from '@/components/Badges';
import { Drawer } from '@/components/Drawer';
import { WatchlistForm } from './WatchlistForm';

export default function WatchlistPage() {
  const { data, error, isLoading } = useSWR<WatchlistEntry[]>(
    '/api/watchlist',
    swrFetcher,
  );

  const [editing, setEditing] = useState<WatchlistEntry | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);

  async function toggleActive(entry: WatchlistEntry) {
    setBusy(entry.id);
    try {
      await api(`/api/watchlist/${entry.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: !entry.is_active }),
      });
      await mutate('/api/watchlist');
    } catch (err) {
      alert(formatError(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove(entry: WatchlistEntry) {
    if (!confirm(`Xoá watchlist entry "${entry.symbol}"?`)) return;
    setBusy(entry.id);
    try {
      await api(`/api/watchlist/${entry.id}`, { method: 'DELETE' });
      await mutate('/api/watchlist');
    } catch (err) {
      alert(formatError(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Cấu hình watchlist</h1>
          <p className="text-sm text-slate-600">
            CRUD mã theo dõi. Cron đọc trực tiếp từ bảng này — không cần restart bot.
          </p>
        </div>
        <div className="flex gap-2">
          <a
            href="/api/watchlist/export"
            className="text-sm px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-50"
          >
            Export YAML
          </a>
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="text-sm px-3 py-1.5 rounded bg-slate-900 text-white hover:bg-slate-700"
          >
            + Thêm mới
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          Lỗi: {formatError(error)}
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-left">
            <tr>
              <th className="px-3 py-2 font-medium">Symbol</th>
              <th className="px-3 py-2 font-medium">Tên</th>
              <th className="px-3 py-2 font-medium">Asset class</th>
              <th className="px-3 py-2 font-medium">Source</th>
              <th className="px-3 py-2 font-medium">Context only</th>
              <th className="px-3 py-2 font-medium">Trạng thái</th>
              <th className="px-3 py-2 font-medium text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-slate-500">Đang tải…</td></tr>
            )}
            {!isLoading && data?.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-slate-500">
                Chưa có entry nào. Bấm <strong>+ Thêm mới</strong> hoặc chạy <code className="font-mono">uv run python main.py seed-watchlist</code> để seed từ YAML.
              </td></tr>
            )}
            {data?.map((entry) => (
              <tr key={entry.id} className="hover:bg-slate-50">
                <td className="px-3 py-2 font-semibold">
                  <Link
                    href={`/charts/${encodeURIComponent(entry.symbol)}`}
                    className="text-slate-900 hover:underline"
                  >
                    {entry.symbol}
                  </Link>
                </td>
                <td className="px-3 py-2 text-slate-700">{entry.name}</td>
                <td className="px-3 py-2"><AssetClassBadge value={entry.asset_class} /></td>
                <td className="px-3 py-2 text-slate-500 font-mono text-xs">{entry.source}</td>
                <td className="px-3 py-2 text-slate-500">
                  {entry.context_only ? '✓' : '—'}
                </td>
                <td className="px-3 py-2">
                  <button
                    type="button"
                    onClick={() => toggleActive(entry)}
                    disabled={busy === entry.id}
                    className="inline-flex"
                    title="Toggle active"
                  >
                    <ActiveBadge value={entry.is_active} />
                  </button>
                </td>
                <td className="px-3 py-2 text-right space-x-2">
                  <button
                    type="button"
                    onClick={() => setEditing(entry)}
                    className="text-slate-700 hover:underline text-xs"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(entry)}
                    disabled={busy === entry.id}
                    className="text-rose-700 hover:underline text-xs disabled:opacity-50"
                  >
                    Xoá
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Drawer
        open={creating}
        onClose={() => setCreating(false)}
        title="Thêm watchlist entry"
      >
        <WatchlistForm
          mode="create"
          onSaved={() => { setCreating(false); mutate('/api/watchlist'); }}
        />
      </Drawer>

      <Drawer
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={`Sửa entry: ${editing?.symbol ?? ''}`}
      >
        {editing && (
          <WatchlistForm
            mode="edit"
            initial={editing}
            onSaved={() => { setEditing(null); mutate('/api/watchlist'); }}
          />
        )}
      </Drawer>
    </div>
  );
}

function formatError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
