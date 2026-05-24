"""Micro Evaluation Service — v2 (checklist coverage).

Aggregates 8 sub-components into one normalized score in [-1, +1]:

    ratio_vs_industry      0.10  — Phần 2.1+2.2 vs industry median (10 ratios)
    ratio_absolute         0.05  — Phần 2.1+2.2 vs checklist thresholds
    news_sentiment         0.10  — keyword / LLM hook (Phase 1)
    income_quality         0.10  — Phần 1.1 (revenue / EPS growth, margins stable)
    balance_health         0.10  — Phần 1.2 (cash, equity trend, no AR/inventory blowup)
    cash_flow_quality      0.25  — Phần 1.3 (CFO > 0, CFO > NP, FCF > 0)  ← highest
    leverage_liquidity     0.20  — Phần 2.3 (D/E, Current, Quick, Interest Cov.)
    operating_efficiency   0.10  — Phần 2.4 (inventory_days, receivable_days)

Weights re-normalize over whichever sub-components are present (score != None).
When the v2 inputs (multi-period history) are absent the engine degrades
gracefully to the v1 behavior (3 sub-scores only).

Emits `news_against` (sentiment < -0.3) so composite engine can gate Tier A,
and a `checklist_report` dict mirroring the 7 sections of
`checklist_vi_mo_doanh_nghiep.md` for the UI + audit log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from finance_bot.ai.prompt import NewsBrief
from finance_bot.logger import logger
from finance_bot.settings import AssetConfig

# ============================================================================
# DTOs — analysis layer; mirror data + db layers but kept independent.
# ============================================================================

@dataclass
class FundamentalSnapshot:
    """One quarter (or FY) of fundamentals for a single ticker."""
    asset_symbol: str
    period: str
    period_end: date
    # v1 ratios
    roa: float | None = None
    roe: float | None = None
    pe: float | None = None
    pb: float | None = None
    # Phần 1.1 — income statement
    revenue: float | None = None
    gross_profit: float | None = None
    net_profit: float | None = None
    eps: float | None = None
    # Phần 1.2 — balance sheet
    cash_and_equivalents: float | None = None
    total_assets: float | None = None
    total_debt: float | None = None
    total_equity: float | None = None
    inventory: float | None = None
    receivables: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    # Phần 1.3 — cash flow
    cfo: float | None = None
    capex: float | None = None
    cff: float | None = None
    fcf: float | None = None
    # Phần 2.1 — valuation extra
    ev_ebitda: float | None = None
    ps: float | None = None
    # Phần 2.2 — profitability extra
    roic: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    # Phần 2.3 — leverage / liquidity
    de_ratio: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    interest_coverage: float | None = None
    # Phần 2.4 — efficiency
    inventory_days: float | None = None
    receivable_days: float | None = None
    ccc: float | None = None
    # Meta
    source: str = "vnstock"
    fetched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class IndustryAverage:
    industry_code: str
    period: str
    roa_avg: float | None = None
    roa_median: float | None = None
    roe_avg: float | None = None
    roe_median: float | None = None
    pe_avg: float | None = None
    pe_median: float | None = None
    pb_avg: float | None = None
    pb_median: float | None = None
    ev_ebitda_avg: float | None = None
    ev_ebitda_median: float | None = None
    ps_avg: float | None = None
    ps_median: float | None = None
    roic_avg: float | None = None
    roic_median: float | None = None
    gross_margin_avg: float | None = None
    gross_margin_median: float | None = None
    net_margin_avg: float | None = None
    net_margin_median: float | None = None
    de_ratio_avg: float | None = None
    de_ratio_median: float | None = None
    n_companies: int = 0
    source: str = "vnstock_screen"


@dataclass
class MicroScore:
    score: float | None
    breakdown: dict[str, float] = field(default_factory=dict)
    news_against: bool = False
    reason: str = ""
    fundamentals_used: FundamentalSnapshot | None = None
    news_count: int = 0
    checklist_report: dict[str, Any] | None = None


# ============================================================================
# Weights — kept in code (not YAML) because they're tied to the formula
# which targets the [-1, +1] composite range. Must sum to ~1.0 when all
# sub-scores are present.
# ============================================================================

_W = {
    "ratio_vs_industry":    0.10,
    "ratio_absolute":       0.05,
    "news_sentiment":       0.10,
    "income_quality":       0.10,
    "balance_health":       0.10,
    "cash_flow_quality":    0.25,   # checklist "Quan trọng nhất"
    "leverage_liquidity":   0.20,
    "operating_efficiency": 0.10,
}

_NEWS_AGAINST_THRESHOLD = -0.3

# Checklist 8.x thresholds. Keep them paired (good, bad, higher_is_better)
# so the linear-interp scorer stays generic.
_ABSOLUTE_BENCHMARK: dict[str, tuple[float, float, bool]] = {
    # Phần 2.2 — sinh lời
    "roa":  (0.08, 0.02, True),    # checklist: ROA > 8%
    "roe":  (0.15, 0.05, True),    # checklist: ROE > 15%
    "roic": (0.12, 0.05, True),    # checklist: ROIC > 12%
    # Phần 2.1 — định giá
    "pe":   (12.0, 25.0, False),
    "pb":   (1.5,  3.0,  False),
    # Phần 2.3 — leverage
    "de_ratio": (1.0, 2.0, False),  # checklist: D/E < 1.0
}


# ============================================================================
# Generic scoring helpers
# ============================================================================

def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _ratio_score(
    asset_val: float | None,
    industry_median: float | None,
    higher_is_better: bool,
) -> float | None:
    if asset_val is None or industry_median is None or industry_median == 0:
        return None
    delta = (asset_val - industry_median) / abs(industry_median)
    return _clamp(delta * (1.0 if higher_is_better else -1.0))


def _absolute_score(
    value: float | None,
    good: float,
    bad: float,
    higher_is_better: bool,
) -> float | None:
    if value is None:
        return None
    if good == bad:
        return 0.0
    norm = (
        (value - bad) / (good - bad)
        if higher_is_better
        else (bad - value) / (bad - good)
    )
    return _clamp(norm * 2.0 - 1.0)


def _fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def _fmt_ratio(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def _fmt_money(v: float | None) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


# ============================================================================
# Checklist-section builder — produces both numeric sub-score AND audit dict
# ============================================================================

def _check(
    name: str, status: str, value: str, threshold: str, reason: str = "",
) -> dict[str, str]:
    return {
        "name": name, "status": status, "value": value,
        "threshold": threshold, "reason": reason,
    }


def _pf(value: float | None, predicate) -> str:
    """Pass / fail / n/a helper for value-based threshold checks."""
    if value is None:
        return "n/a"
    return "pass" if predicate(value) else "fail"


def _section_score(checks: list[dict]) -> float | None:
    """Score = (pass - fail) / total_evaluated. n/a checks excluded."""
    evaluated = [c for c in checks if c["status"] in ("pass", "fail")]
    if not evaluated:
        return None
    passed = sum(1 for c in evaluated if c["status"] == "pass")
    failed = len(evaluated) - passed
    return (passed - failed) / len(evaluated)


def _summarize_section(code: str, name: str, checks: list[dict]) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "checks": checks,
        "passed": sum(1 for c in checks if c["status"] == "pass"),
        "failed": sum(1 for c in checks if c["status"] == "fail"),
        "n_a":    sum(1 for c in checks if c["status"] == "n/a"),
    }


# ----------------------------------------------------------------------------
# Section 1.1 — Kết quả kinh doanh
# ----------------------------------------------------------------------------

def _yoy(latest: float | None, prior: float | None) -> float | None:
    if latest is None or prior is None or prior == 0:
        return None
    return (latest - prior) / abs(prior)


def _section_1_1_income(history: list[FundamentalSnapshot]) -> dict[str, Any]:
    """Doanh thu / lợi nhuận / EPS tăng trưởng đều, biên LN ổn định."""
    checks: list[dict] = []
    if not history:
        checks.append(_check("Doanh thu YoY", "n/a", "n/a", ">0", "no fundamentals"))
        return _summarize_section("1.1", "Kết quả kinh doanh", checks)

    latest = history[0]
    prior_yoy = history[4] if len(history) >= 5 else None
    prior_3y  = history[11] if len(history) >= 12 else None

    rev_yoy = _yoy(latest.revenue, prior_yoy.revenue if prior_yoy else None)
    rev_reason = "" if rev_yoy is None else (
        "doanh thu tăng so với cùng kỳ" if rev_yoy > 0
        else "doanh thu giảm so với cùng kỳ"
    )
    checks.append(_check(
        "Doanh thu YoY > 0", _pf(rev_yoy, lambda v: v > 0),
        _fmt_pct(rev_yoy), ">0", rev_reason,
    ))

    net_yoy = _yoy(latest.net_profit, prior_yoy.net_profit if prior_yoy else None)
    checks.append(_check(
        "LN ròng YoY > 0", _pf(net_yoy, lambda v: v > 0), _fmt_pct(net_yoy), ">0",
    ))

    eps_yoy = _yoy(latest.eps, prior_yoy.eps if prior_yoy else None)
    checks.append(_check(
        "EPS YoY > 0", _pf(eps_yoy, lambda v: v > 0), _fmt_pct(eps_yoy), ">0",
    ))

    # 3-year CAGR (compounded). 12 quarters ≈ 3 years.
    cagr: float | None = None
    if prior_3y is not None and prior_3y.revenue and latest.revenue:
        try:
            cagr = (latest.revenue / prior_3y.revenue) ** (1 / 3) - 1
        except Exception:
            cagr = None
    checks.append(_check(
        "CAGR doanh thu 3 năm > 10%",
        "pass" if cagr is not None and cagr > 0.10 else ("fail" if cagr is not None else "n/a"),
        _fmt_pct(cagr), ">10%/năm",
    ))

    # Gross margin stability — recent 4Q stddev relative to mean.
    gm = [s.gross_margin for s in history[:4] if s.gross_margin is not None]
    if len(gm) >= 2:
        mean = sum(gm) / len(gm)
        var = sum((x - mean) ** 2 for x in gm) / len(gm)
        stddev = var ** 0.5
        # Acceptable: stddev < 30% of mean (very rough heuristic).
        stable = stddev < abs(mean) * 0.30 if mean else False
        checks.append(_check(
            "Biên LN gộp ổn định",
            "pass" if stable else "fail",
            f"σ={_fmt_pct(stddev)}", "σ < 30% × |trung bình|",
            "biên LN gộp 4 quý gần nhất biến động " + ("thấp" if stable else "lớn"),
        ))
    else:
        checks.append(_check("Biên LN gộp ổn định", "n/a", "n/a", "σ < 30%", "thiếu dữ liệu"))

    return _summarize_section("1.1", "Kết quả kinh doanh", checks)


# ----------------------------------------------------------------------------
# Section 1.2 — Bảng cân đối
# ----------------------------------------------------------------------------

def _section_1_2_balance(history: list[FundamentalSnapshot]) -> dict[str, Any]:
    checks: list[dict] = []
    if not history:
        checks.append(_check("Tiền mặt > 0", "n/a", "n/a", ">0", "no fundamentals"))
        return _summarize_section("1.2", "Bảng cân đối", checks)

    latest = history[0]
    prior_yoy = history[4] if len(history) >= 5 else None

    checks.append(_check(
        "Tiền & tương đương > 0",
        "pass" if latest.cash_and_equivalents and latest.cash_and_equivalents > 0
        else ("fail" if latest.cash_and_equivalents is not None else "n/a"),
        _fmt_money(latest.cash_and_equivalents), ">0",
    ))

    equity_yoy = _yoy(latest.total_equity, prior_yoy.total_equity if prior_yoy else None)
    checks.append(_check(
        "Vốn CSH YoY > 0", _pf(equity_yoy, lambda v: v > 0),
        _fmt_pct(equity_yoy), ">0",
    ))

    # Receivables không tăng nhanh hơn revenue.
    ar_yoy = _yoy(latest.receivables, prior_yoy.receivables if prior_yoy else None)
    rev_yoy = _yoy(latest.revenue, prior_yoy.revenue if prior_yoy else None)
    if ar_yoy is not None and rev_yoy is not None:
        ok = ar_yoy <= rev_yoy + 0.05  # 5pp tolerance
        checks.append(_check(
            "Khoản phải thu KHÔNG tăng nhanh hơn doanh thu",
            "pass" if ok else "fail",
            f"AR {_fmt_pct(ar_yoy)} vs Rev {_fmt_pct(rev_yoy)}",
            "AR YoY ≤ Rev YoY + 5pp",
        ))
    else:
        checks.append(_check(
            "Khoản phải thu vs doanh thu", "n/a", "n/a",
            "AR YoY ≤ Rev YoY", "thiếu dữ liệu",
        ))

    # Inventory không tích lũy bất thường.
    inv_yoy = _yoy(latest.inventory, prior_yoy.inventory if prior_yoy else None)
    if inv_yoy is not None and rev_yoy is not None:
        ok = inv_yoy <= rev_yoy + 0.10
        checks.append(_check(
            "Tồn kho KHÔNG tích lũy bất thường",
            "pass" if ok else "fail",
            f"Inv {_fmt_pct(inv_yoy)} vs Rev {_fmt_pct(rev_yoy)}",
            "Inv YoY ≤ Rev YoY + 10pp",
        ))
    else:
        checks.append(_check(
            "Tồn kho vs doanh thu", "n/a", "n/a",
            "Inv YoY ≤ Rev YoY", "thiếu dữ liệu",
        ))

    return _summarize_section("1.2", "Bảng cân đối", checks)


# ----------------------------------------------------------------------------
# Section 1.3 — Lưu chuyển tiền tệ (CHECKLIST "QUAN TRỌNG NHẤT")
# ----------------------------------------------------------------------------

def _section_1_3_cash_flow(history: list[FundamentalSnapshot]) -> dict[str, Any]:
    checks: list[dict] = []
    if not history:
        checks.append(_check("CFO > 0", "n/a", "n/a", ">0", "no fundamentals"))
        return _summarize_section("1.3", "Lưu chuyển tiền tệ", checks)

    latest = history[0]

    checks.append(_check(
        "CFO > 0",
        "pass" if latest.cfo is not None and latest.cfo > 0
        else ("fail" if latest.cfo is not None else "n/a"),
        _fmt_money(latest.cfo), ">0",
        "dòng tiền HĐKD dương" if latest.cfo and latest.cfo > 0 else "dòng tiền HĐKD âm",
    ))

    if latest.cfo is not None and latest.net_profit is not None:
        checks.append(_check(
            "CFO > Lợi nhuận ròng",
            "pass" if latest.cfo > latest.net_profit else "fail",
            f"CFO {_fmt_money(latest.cfo)} vs NP {_fmt_money(latest.net_profit)}",
            "CFO > NP (LN chất lượng cao)",
        ))
    else:
        checks.append(_check("CFO > Lợi nhuận ròng", "n/a", "n/a", "CFO > NP", "thiếu dữ liệu"))

    checks.append(_check(
        "FCF > 0 (CFO - Capex)",
        "pass" if latest.fcf is not None and latest.fcf > 0
        else ("fail" if latest.fcf is not None else "n/a"),
        _fmt_money(latest.fcf), ">0",
    ))

    # 4-quarter rolling CFO sign.
    cfos = [s.cfo for s in history[:4] if s.cfo is not None]
    if len(cfos) >= 2:
        rolling = sum(cfos)
        checks.append(_check(
            "CFO 4 quý gần nhất > 0",
            "pass" if rolling > 0 else "fail",
            _fmt_money(rolling), ">0 (rolling)",
        ))
    else:
        checks.append(_check("CFO 4 quý rolling", "n/a", "n/a", ">0", "thiếu lịch sử"))

    # CFF không phụ thuộc vào vay để bù đắp HĐKD (CFO âm mà CFF dương lớn = bù đắp).
    if latest.cfo is not None and latest.cff is not None:
        bad_pattern = latest.cfo < 0 and latest.cff > abs(latest.cfo)
        checks.append(_check(
            "CFF không bù đắp CFO âm",
            "fail" if bad_pattern else "pass",
            f"CFO {_fmt_money(latest.cfo)} / CFF {_fmt_money(latest.cff)}",
            "tránh vay ngắn hạn để bù dòng tiền hoạt động",
        ))
    else:
        checks.append(_check(
            "CFF không bù đắp CFO âm", "n/a", "n/a",
            "CFO ≥ 0 hoặc CFF ≤ |CFO|", "thiếu dữ liệu",
        ))

    return _summarize_section("1.3", "Lưu chuyển tiền tệ", checks)


# ----------------------------------------------------------------------------
# Section 2.1 — Định giá (uses both vs-industry + absolute thresholds)
# ----------------------------------------------------------------------------

def _section_2_1_valuation(
    fund: FundamentalSnapshot | None,
    industry: IndustryAverage | None,
) -> dict[str, Any]:
    checks: list[dict] = []
    if fund is None:
        checks.append(_check("P/E", "n/a", "n/a", "<25", "no fundamentals"))
        return _summarize_section("2.1", "Định giá", checks)

    for ratio_name, label, good, bad in (
        ("pe",  "P/E",        12.0, 25.0),
        ("pb",  "P/B",        1.5,  3.0),
        ("ps",  "P/S",        1.5,  4.0),
        ("ev_ebitda", "EV/EBITDA", 8.0, 15.0),
    ):
        v = getattr(fund, ratio_name, None)
        if v is None:
            checks.append(_check(f"{label} hợp lý", "n/a", "n/a", f"<{bad}", "thiếu dữ liệu"))
            continue
        status = "pass" if v < bad else "fail"
        if v < good:
            reason = "định giá rẻ"
        elif v < bad:
            reason = "hợp lý"
        else:
            reason = "đắt"
        checks.append(_check(f"{label} < {bad}", status, _fmt_ratio(v), f"<{bad}", reason))

    if industry is not None:
        for ratio_name, median_name, label in (
            ("pe", "pe_median", "P/E vs ngành"),
            ("pb", "pb_median", "P/B vs ngành"),
        ):
            v = getattr(fund, ratio_name, None)
            m = getattr(industry, median_name, None)
            if v is None or m is None:
                checks.append(_check(label, "n/a", "n/a", "<= median", "thiếu dữ liệu"))
                continue
            checks.append(_check(
                label, "pass" if v <= m else "fail",
                f"{_fmt_ratio(v)} vs {_fmt_ratio(m)}", "≤ median",
                "rẻ hơn ngành" if v <= m else "đắt hơn ngành",
            ))

    return _summarize_section("2.1", "Định giá", checks)


# ----------------------------------------------------------------------------
# Section 2.2 — Sinh lời
# ----------------------------------------------------------------------------

def _section_2_2_profitability(
    fund: FundamentalSnapshot | None,
    history: list[FundamentalSnapshot],
) -> dict[str, Any]:
    checks: list[dict] = []
    if fund is None:
        checks.append(_check("ROE > 15%", "n/a", "n/a", ">15%", "no fundamentals"))
        return _summarize_section("2.2", "Sinh lời", checks)

    for ratio, threshold, label in (
        ("roe", 0.15, "ROE > 15%"),
        ("roa", 0.08, "ROA > 8%"),
        ("roic", 0.12, "ROIC > 12%"),
    ):
        v = getattr(fund, ratio, None)
        if v is None:
            checks.append(_check(label, "n/a", "n/a", f">{threshold * 100:.0f}%", "thiếu dữ liệu"))
            continue
        checks.append(_check(
            label, "pass" if v > threshold else "fail",
            _fmt_pct(v), f">{threshold * 100:.0f}%",
        ))

    for ratio, label in (("gross_margin", "Biên LN gộp"), ("net_margin", "Biên LN ròng")):
        v = getattr(fund, ratio, None)
        checks.append(_check(
            label + " > 0",
            "pass" if v is not None and v > 0 else ("fail" if v is not None else "n/a"),
            _fmt_pct(v), ">0 (chi tiết theo ngành)",
        ))

    # ROE stable over last 3 years (12 quarters).
    roes = [s.roe for s in history[:12] if s.roe is not None]
    if len(roes) >= 6:
        mean = sum(roes) / len(roes)
        stable = all(r > 0 for r in roes) and mean > 0.10
        checks.append(_check(
            "ROE ổn định 3 năm",
            "pass" if stable else "fail",
            f"μ={_fmt_pct(mean)}", "trung bình > 10% và không âm",
        ))
    else:
        checks.append(_check("ROE ổn định 3 năm", "n/a", "n/a", "12Q lịch sử", "thiếu lịch sử"))

    return _summarize_section("2.2", "Sinh lời", checks)


# ----------------------------------------------------------------------------
# Section 2.3 — Nợ & thanh khoản
# ----------------------------------------------------------------------------

def _section_2_3_leverage(fund: FundamentalSnapshot | None) -> dict[str, Any]:
    checks: list[dict] = []
    if fund is None:
        checks.append(_check("D/E < 1.0", "n/a", "n/a", "<1.0", "no fundamentals"))
        return _summarize_section("2.3", "Nợ & thanh khoản", checks)

    for ratio, threshold, higher_better, label in (
        ("de_ratio", 1.0, False, "D/E < 1.0"),
        ("current_ratio", 1.5, True, "Current Ratio > 1.5"),
        ("quick_ratio", 1.0, True, "Quick Ratio > 1.0"),
        ("interest_coverage", 3.0, True, "Interest Coverage > 3x"),
    ):
        v = getattr(fund, ratio, None)
        if v is None:
            checks.append(_check(
                label, "n/a", "n/a",
                " ".join(label.split()[-2:]), "thiếu dữ liệu",
            ))
            continue
        passed = v > threshold if higher_better else v < threshold
        checks.append(_check(label, "pass" if passed else "fail", _fmt_ratio(v),
                             f">{threshold}" if higher_better else f"<{threshold}"))

    return _summarize_section("2.3", "Nợ & thanh khoản", checks)


# ----------------------------------------------------------------------------
# Section 2.4 — Hiệu quả hoạt động
# ----------------------------------------------------------------------------

def _section_2_4_efficiency(history: list[FundamentalSnapshot]) -> dict[str, Any]:
    checks: list[dict] = []
    if not history:
        checks.append(_check("Vòng quay tồn kho", "n/a", "n/a", "ổn định", "no fundamentals"))
        return _summarize_section("2.4", "Hiệu quả hoạt động", checks)

    latest = history[0]
    prior_yoy = history[4] if len(history) >= 5 else None

    # Inventory days stable or declining.
    if latest.inventory_days is not None and prior_yoy and prior_yoy.inventory_days is not None:
        improving = latest.inventory_days <= prior_yoy.inventory_days * 1.05
        checks.append(_check(
            "Vòng quay tồn kho ổn định/cải thiện",
            "pass" if improving else "fail",
            f"{_fmt_ratio(latest.inventory_days)} d vs {_fmt_ratio(prior_yoy.inventory_days)} d",
            "≤ +5% YoY",
        ))
    else:
        checks.append(_check("Vòng quay tồn kho", "n/a",
                             _fmt_ratio(latest.inventory_days), "ổn định",
                             "thiếu lịch sử"))

    if latest.receivable_days is not None and prior_yoy and prior_yoy.receivable_days is not None:
        improving = latest.receivable_days <= prior_yoy.receivable_days * 1.05
        checks.append(_check(
            "Vòng quay phải thu ổn định/cải thiện",
            "pass" if improving else "fail",
            f"{_fmt_ratio(latest.receivable_days)} d vs {_fmt_ratio(prior_yoy.receivable_days)} d",
            "≤ +5% YoY",
        ))
    else:
        checks.append(_check("Vòng quay phải thu", "n/a",
                             _fmt_ratio(latest.receivable_days), "ổn định",
                             "thiếu lịch sử"))

    # Receivable days absolute threshold (no industry-specific tuning).
    if latest.receivable_days is not None:
        checks.append(_check(
            "Số ngày thu tiền < 60",
            "pass" if latest.receivable_days < 60 else "fail",
            f"{_fmt_ratio(latest.receivable_days)} d", "<60 d",
        ))
    else:
        checks.append(_check("Số ngày thu tiền < 60", "n/a", "n/a", "<60 d", "thiếu dữ liệu"))

    return _summarize_section("2.4", "Hiệu quả hoạt động", checks)


# ============================================================================
# Existing sub-scores (extended to new ratio set)
# ============================================================================

_INDUSTRY_PAIRS: tuple[tuple[str, str, bool], ...] = (
    # (asset_field, industry_median_field, higher_is_better)
    ("roa",  "roa_median",  True),
    ("roe",  "roe_median",  True),
    ("pe",   "pe_median",   False),
    ("pb",   "pb_median",   False),
    ("ev_ebitda", "ev_ebitda_median", False),
    ("ps",   "ps_median",   False),
    ("roic", "roic_median", True),
    ("gross_margin", "gross_margin_median", True),
    ("net_margin",   "net_margin_median",   True),
    ("de_ratio",     "de_ratio_median",     False),
)


def _score_ratio_vs_industry(
    fund: FundamentalSnapshot, industry: IndustryAverage,
) -> float | None:
    subs: list[float] = []
    for asset_field, ind_field, higher_better in _INDUSTRY_PAIRS:
        s = _ratio_score(
            getattr(fund, asset_field, None),
            getattr(industry, ind_field, None),
            higher_is_better=higher_better,
        )
        if s is not None:
            subs.append(s)
    return sum(subs) / len(subs) if subs else None


def _score_ratio_absolute(fund: FundamentalSnapshot) -> float | None:
    subs: list[float] = []
    for ratio, (good, bad, higher_better) in _ABSOLUTE_BENCHMARK.items():
        s = _absolute_score(getattr(fund, ratio, None), good, bad, higher_better)
        if s is not None:
            subs.append(s)
    return sum(subs) / len(subs) if subs else None


# ============================================================================
# News sentiment (unchanged from v1)
# ============================================================================

_NEG_KEYWORDS = (
    "phạt", "vi phạm", "điều tra", "thua lỗ", "âm", "rút", "bán tháo",
    "thanh tra", "khởi tố", "cảnh báo", "giảm sàn", "lao dốc",
)
_POS_KEYWORDS = (
    "lãi kỷ lục", "lãi", "tăng trưởng", "kỷ lục", "vượt kế hoạch",
    "mở rộng", "trúng thầu", "M&A", "cổ tức", "khởi sắc",
)


def _rule_based_sentiment(news: NewsBrief) -> float:
    text = f"{news.title} {news.summary or ''}".lower()
    neg = sum(1 for kw in _NEG_KEYWORDS if kw.lower() in text)
    pos = sum(1 for kw in _POS_KEYWORDS if kw.lower() in text)
    if neg == 0 and pos == 0:
        return 0.0
    return _clamp((pos - neg) / max(1.0, pos + neg))


def _llm_sentiment(news: NewsBrief) -> float:
    """Hook for Claude-based per-news sentiment. Default: rule-based."""
    return _rule_based_sentiment(news)


def _score_news_sentiment(news: list[NewsBrief]) -> float | None:
    if not news:
        return None
    scores: list[float] = []
    for n in news[:8]:
        cached = getattr(n, "sentiment", None)
        if isinstance(cached, (int, float, Decimal)):
            scores.append(float(cached))
            continue
        try:
            scores.append(_llm_sentiment(n))
        except Exception:
            logger.debug("Sentiment LLM failed for news {!r}; using rule-based", n.title)
            scores.append(_rule_based_sentiment(n))
    return sum(scores) / len(scores) if scores else None


# ============================================================================
# Top-level aggregator
# ============================================================================

def compute_micro_score(
    asset: AssetConfig,
    fundamentals: FundamentalSnapshot | None,
    industry_avg: IndustryAverage | None,
    news: list[NewsBrief],
    history: list[FundamentalSnapshot] | None = None,
) -> MicroScore:
    """Compute MicroScore across 8 sub-scores + 7-section checklist report.

    `history` is newest → oldest, includes `fundamentals` as element 0 when
    provided. When `history` is None or empty, only the legacy sub-scores
    (ratio_vs_industry, ratio_absolute, news_sentiment) can fire.
    """
    del asset  # accepted for symmetry; class-specific tuning may use it later
    history = history or ([fundamentals] if fundamentals else [])

    # Build the 7 checklist sections + their numeric sub-scores.
    sec_1_1 = _section_1_1_income(history)
    sec_1_2 = _section_1_2_balance(history)
    sec_1_3 = _section_1_3_cash_flow(history)
    sec_2_1 = _section_2_1_valuation(fundamentals, industry_avg)
    sec_2_2 = _section_2_2_profitability(fundamentals, history)
    sec_2_3 = _section_2_3_leverage(fundamentals)
    sec_2_4 = _section_2_4_efficiency(history)

    sub_scores: dict[str, float | None] = {
        "income_quality":       _section_score(sec_1_1["checks"]),
        "balance_health":       _section_score(sec_1_2["checks"]),
        "cash_flow_quality":    _section_score(sec_1_3["checks"]),
        "leverage_liquidity":   _section_score(sec_2_3["checks"]),
        "operating_efficiency": _section_score(sec_2_4["checks"]),
        "ratio_vs_industry":    (
            _score_ratio_vs_industry(fundamentals, industry_avg)
            if fundamentals and industry_avg else None
        ),
        "ratio_absolute":       _score_ratio_absolute(fundamentals) if fundamentals else None,
        "news_sentiment":       _score_news_sentiment(news),
    }

    breakdown: dict[str, float] = {}
    weighted = 0.0
    weight_sum = 0.0
    for name, score in sub_scores.items():
        if score is None:
            continue
        weight = _W[name]
        weighted += weight * score
        weight_sum += weight
        breakdown[name] = score

    checklist_report = {
        "section_1_1": sec_1_1,
        "section_1_2": sec_1_2,
        "section_1_3": sec_1_3,
        "section_2_1": sec_2_1,
        "section_2_2": sec_2_2,
        "section_2_3": sec_2_3,
        "section_2_4": sec_2_4,
    }

    if weight_sum == 0:
        return MicroScore(
            score=None,
            reason="no_micro_data",
            news_count=len(news),
            checklist_report=checklist_report,
            fundamentals_used=fundamentals,
        )

    score = weighted / weight_sum
    sentiment = sub_scores["news_sentiment"]
    news_against = sentiment is not None and sentiment < _NEWS_AGAINST_THRESHOLD

    parts = [f"{k}={v:+.2f}" for k, v in breakdown.items()]
    reason = f"micro={score:+.2f} ({', '.join(parts)})"
    if news_against:
        reason += " — news ngược chiều"

    return MicroScore(
        score=score,
        breakdown=breakdown,
        news_against=news_against,
        reason=reason,
        fundamentals_used=fundamentals,
        news_count=len(news),
        checklist_report=checklist_report,
    )
