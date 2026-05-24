'use client';

import useSWR from 'swr';
import Link from 'next/link';
import { swrFetcher } from '@/lib/api';
import type { WatchlistEntry } from '@/lib/types';
import { AssetClassBadge } from '@/components/Badges';

export default function ChartsIndexPage() {
  const { data, isLoading } = useSWR<WatchlistEntry[]>(
    '/api/watchlist?only_active=true',
    swrFetcher,
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Đồ thị giá + chỉ báo</h1>
        <p className="text-sm text-slate-600">
          Chọn 1 mã trong watchlist để xem candlestick + indicator overlay.
        </p>
      </div>
      {isLoading && <p className="text-slate-500">Đang tải…</p>}
      <ul className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {data?.map((entry) => (
          <li key={entry.id}>
            <Link
              href={`/charts/${encodeURIComponent(entry.symbol)}`}
              className="block rounded-lg border border-slate-200 bg-white p-3 hover:border-slate-400"
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold">{entry.symbol}</span>
                <AssetClassBadge value={entry.asset_class} />
              </div>
              <div className="text-xs text-slate-500 mt-1">{entry.name}</div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
