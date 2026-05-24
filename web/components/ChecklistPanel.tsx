'use client';

import clsx from 'clsx';
import type {
  CheckStatus,
  ChecklistCheck,
  ChecklistReport,
  ChecklistSection,
} from '@/lib/types';

const SECTION_ORDER: (keyof ChecklistReport)[] = [
  'section_1_1',
  'section_1_2',
  'section_1_3',
  'section_2_1',
  'section_2_2',
  'section_2_3',
  'section_2_4',
];

const STATUS_ICON: Record<CheckStatus, string> = {
  pass: '✅',
  fail: '❌',
  'n/a': '➖',
};

const STATUS_COLOR: Record<CheckStatus, string> = {
  pass: 'text-emerald-700',
  fail: 'text-rose-700',
  'n/a': 'text-slate-400',
};

/** Render the micro checklist (7 sections of `checklist_vi_mo_doanh_nghiep.md`).
 *
 *  Pulls from `signal.indicators.micro_score.checklist_report`. Renders
 *  nothing when the field is missing (legacy signals from before the v2
 *  rollout) — caller decides whether to show a placeholder.
 */
export function ChecklistPanel({ report }: { report: ChecklistReport | null }) {
  if (!report) return null;

  return (
    <div className="space-y-2">
      {SECTION_ORDER.map((key) => {
        const section = report[key];
        if (!section) return null;
        return <SectionBlock key={key} section={section} />;
      })}
    </div>
  );
}

function SectionBlock({ section }: { section: ChecklistSection }) {
  const total = section.passed + section.failed;
  const passRatio = total > 0 ? section.passed / total : 0;

  return (
    <details className="rounded border border-slate-200 bg-white open:bg-slate-50/40">
      <summary className="cursor-pointer px-3 py-2 flex items-center gap-2 text-sm select-none">
        <span className="text-slate-500 font-mono w-8 text-xs">{section.code}</span>
        <span className="flex-1 font-medium text-slate-800">{section.name}</span>
        <StatusBar
          passed={section.passed}
          failed={section.failed}
          n_a={section.n_a}
          passRatio={passRatio}
        />
      </summary>
      <ul className="divide-y divide-slate-100 px-3 pb-2">
        {section.checks.map((check, idx) => (
          <CheckRow key={`${section.code}-${idx}`} check={check} />
        ))}
      </ul>
    </details>
  );
}

function StatusBar({
  passed,
  failed,
  n_a,
  passRatio,
}: {
  passed: number;
  failed: number;
  n_a: number;
  passRatio: number;
}) {
  let badgeColor = 'bg-slate-100 text-slate-600';
  if (passed + failed > 0) {
    if (passRatio >= 0.75) badgeColor = 'bg-emerald-100 text-emerald-800';
    else if (passRatio >= 0.4) badgeColor = 'bg-amber-100 text-amber-800';
    else badgeColor = 'bg-rose-100 text-rose-800';
  }
  return (
    <span className={clsx('text-xs font-mono px-2 py-0.5 rounded', badgeColor)}>
      {passed}✓ {failed}✗ {n_a > 0 ? `${n_a}—` : ''}
    </span>
  );
}

function CheckRow({ check }: { check: ChecklistCheck }) {
  return (
    <li className="py-1.5 flex items-start gap-2 text-xs">
      <span className="text-base leading-4">{STATUS_ICON[check.status]}</span>
      <div className="flex-1 min-w-0">
        <div className={clsx('font-medium', STATUS_COLOR[check.status])}>
          {check.name}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-slate-500 mt-0.5">
          <span>
            <span className="text-slate-400">value:</span>{' '}
            <span className="tabular text-slate-700">{check.value}</span>
          </span>
          <span>
            <span className="text-slate-400">ngưỡng:</span>{' '}
            <span className="text-slate-700">{check.threshold}</span>
          </span>
        </div>
        {check.reason && (
          <div className="text-slate-500 mt-0.5">{check.reason}</div>
        )}
      </div>
    </li>
  );
}

/** Helper to safely extract the report from the loosely-typed `indicators` JSON. */
export function extractChecklistReport(
  indicators: Record<string, unknown> | undefined | null,
): ChecklistReport | null {
  if (!indicators || typeof indicators !== 'object') return null;
  const micro = (indicators as { micro_score?: unknown }).micro_score;
  if (!micro || typeof micro !== 'object') return null;
  const report = (micro as { checklist_report?: unknown }).checklist_report;
  if (!report || typeof report !== 'object') return null;
  return report as ChecklistReport;
}
