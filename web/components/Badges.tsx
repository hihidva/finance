import clsx from 'clsx';
import type { AssetClass, Side, Tier, UserDecision } from '@/lib/types';

const ASSET_CLASS_LABEL: Record<AssetClass, string> = {
  vn_stock: 'VN stock',
  crypto: 'Crypto',
  commodity: 'Commodity',
  fx_index: 'FX index',
};

const ASSET_CLASS_STYLE: Record<AssetClass, string> = {
  vn_stock: 'bg-emerald-100 text-emerald-700',
  crypto: 'bg-orange-100 text-orange-700',
  commodity: 'bg-amber-100 text-amber-700',
  fx_index: 'bg-slate-200 text-slate-700',
};

export function AssetClassBadge({ value }: { value: AssetClass }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium',
        ASSET_CLASS_STYLE[value],
      )}
    >
      {ASSET_CLASS_LABEL[value]}
    </span>
  );
}

const TIER_STYLE: Record<Tier, string> = {
  A: 'bg-emerald-700 text-white',
  B: 'bg-amber-500 text-white',
  C: 'bg-slate-400 text-white',
};

export function TierBadge({ value }: { value: Tier }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center justify-center w-7 h-6 rounded text-xs font-bold',
        TIER_STYLE[value],
      )}
    >
      {value}
    </span>
  );
}

const SIDE_STYLE: Record<Side, string> = {
  buy: 'text-emerald-700',
  sell: 'text-rose-700',
  hold: 'text-slate-500',
};

export function SideText({ value }: { value: Side }) {
  return (
    <span className={clsx('font-semibold uppercase text-xs', SIDE_STYLE[value])}>
      {value}
    </span>
  );
}

export function DecisionBadge({ value }: { value: UserDecision | null }) {
  if (value === 'entered')
    return <span className="text-emerald-700 text-sm">✅ Đã vào lệnh</span>;
  if (value === 'skipped')
    return <span className="text-slate-500 text-sm">⏭ Bỏ qua</span>;
  return <span className="text-slate-400 text-sm">— chưa phản hồi</span>;
}

export function ActiveBadge({ value }: { value: boolean }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium',
        value
          ? 'bg-emerald-100 text-emerald-700'
          : 'bg-slate-200 text-slate-600',
      )}
    >
      {value ? 'Active' : 'Paused'}
    </span>
  );
}
