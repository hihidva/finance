"""Unit tests for the v2 micro checklist engine (Phần 1.1 – 2.4).

Focused on the 5 new sub-scores and the checklist_report shape.
"""
from __future__ import annotations

from datetime import date

from finance_bot.analysis.evaluation_micro import (
    FundamentalSnapshot,
    IndustryAverage,
    compute_micro_score,
)


def _snap(period: str, period_end: date, **kwargs) -> FundamentalSnapshot:
    return FundamentalSnapshot(
        asset_symbol="FPT", period=period, period_end=period_end, **kwargs,
    )


# ---------------------------------------------------------------------------
# Top-level shape: 7 checklist sections are always emitted
# ---------------------------------------------------------------------------
def test_empty_input_returns_7_sections(asset_fpt):
    res = compute_micro_score(asset_fpt, None, None, [])
    assert res.score is None
    assert res.checklist_report is not None
    assert set(res.checklist_report.keys()) == {
        "section_1_1", "section_1_2", "section_1_3",
        "section_2_1", "section_2_2", "section_2_3", "section_2_4",
    }


def test_section_codes_match_checklist_doc(asset_fpt):
    res = compute_micro_score(asset_fpt, None, None, [])
    codes = {k: v["code"] for k, v in res.checklist_report.items()}
    assert codes == {
        "section_1_1": "1.1", "section_1_2": "1.2", "section_1_3": "1.3",
        "section_2_1": "2.1", "section_2_2": "2.2", "section_2_3": "2.3",
        "section_2_4": "2.4",
    }


# ---------------------------------------------------------------------------
# Section 1.3 — Cash flow (HIGHEST weight, per checklist "quan trọng nhất")
# ---------------------------------------------------------------------------
def test_cash_flow_quality_all_pass(asset_fpt):
    # CFO > 0, CFO > NP, FCF > 0, CFF small, rolling CFO > 0.
    history = [
        _snap("2025-Q4", date(2025, 12, 31),
              cfo=2000.0, net_profit=1500.0, capex=500.0, fcf=1500.0, cff=-300.0),
        _snap("2025-Q3", date(2025, 9, 30), cfo=1900.0),
        _snap("2025-Q2", date(2025, 6, 30), cfo=1800.0),
        _snap("2025-Q1", date(2025, 3, 31), cfo=1700.0),
    ]
    res = compute_micro_score(asset_fpt, history[0], None, [], history=history)
    sec = res.checklist_report["section_1_3"]
    assert sec["failed"] == 0
    assert sec["passed"] >= 4
    assert res.breakdown["cash_flow_quality"] > 0.0


def test_cash_flow_quality_fails_when_cfo_negative(asset_fpt):
    history = [
        _snap("2025-Q4", date(2025, 12, 31),
              cfo=-500.0, net_profit=200.0, capex=1000.0, fcf=-1500.0, cff=2000.0),
    ]
    res = compute_micro_score(asset_fpt, history[0], None, [], history=history)
    sec = res.checklist_report["section_1_3"]
    # CFO < 0, CFO < NP, FCF < 0, CFF bù đắp CFO âm → 4 fail; rolling = n/a
    assert sec["failed"] >= 3
    assert res.breakdown["cash_flow_quality"] < 0.0


def test_cash_flow_weight_is_highest(asset_fpt):
    """When ALL sub-scores fire at +1, cash_flow_quality must contribute the most."""
    from finance_bot.analysis.evaluation_micro import _W
    assert _W["cash_flow_quality"] == max(_W.values())


# ---------------------------------------------------------------------------
# Section 1.1 — Income quality (revenue / EPS growth, margin stability)
# ---------------------------------------------------------------------------
def test_income_quality_yoy_growth(asset_fpt):
    # 5Q history → can compute YoY (Q4-2025 vs Q4-2024).
    history = [
        _snap("2025-Q4", date(2025, 12, 31),
              revenue=10000.0, net_profit=1500.0, eps=2500.0, gross_margin=0.30),
        *[_snap(f"2025-Q{q}", date(2025, q * 3, 30), revenue=9000.0 + q * 100,
                gross_margin=0.29) for q in (3, 2, 1)],
        _snap("2024-Q4", date(2024, 12, 31),
              revenue=9000.0, net_profit=1300.0, eps=2200.0, gross_margin=0.28),
    ]
    res = compute_micro_score(asset_fpt, history[0], None, [], history=history)
    sec = res.checklist_report["section_1_1"]
    assert sec["passed"] >= 3
    rev_check = next(c for c in sec["checks"] if "Doanh thu YoY" in c["name"])
    assert rev_check["status"] == "pass"


def test_income_quality_3y_cagr_requires_12q(asset_fpt):
    short_history = [_snap("2025-Q4", date(2025, 12, 31), revenue=10000.0)]
    res = compute_micro_score(asset_fpt, short_history[0], None, [], history=short_history)
    cagr_check = next(
        c for c in res.checklist_report["section_1_1"]["checks"] if "CAGR" in c["name"]
    )
    assert cagr_check["status"] == "n/a"


# ---------------------------------------------------------------------------
# Section 2.3 — Leverage & liquidity (uses checklist thresholds)
# ---------------------------------------------------------------------------
def test_leverage_liquidity_uses_checklist_thresholds(asset_fpt):
    # Exactly on the checklist boundary: D/E=1.0 (not <1.0 → fail), Current=1.5 (not >1.5 → fail),
    # Quick=1.0 (not >1.0 → fail), Interest=3.0 (not >3.0 → fail).
    fund = _snap("2025-Q4", date(2025, 12, 31),
                 de_ratio=1.0, current_ratio=1.5, quick_ratio=1.0, interest_coverage=3.0)
    res = compute_micro_score(asset_fpt, fund, None, [], history=[fund])
    sec = res.checklist_report["section_2_3"]
    assert sec["failed"] == 4  # all four boundary cases fail strict inequalities


def test_leverage_liquidity_passes_above_thresholds(asset_fpt):
    fund = _snap("2025-Q4", date(2025, 12, 31),
                 de_ratio=0.4, current_ratio=2.5, quick_ratio=1.8, interest_coverage=10.0)
    res = compute_micro_score(asset_fpt, fund, None, [], history=[fund])
    sec = res.checklist_report["section_2_3"]
    assert sec["passed"] == 4
    assert res.breakdown["leverage_liquidity"] == 1.0


# ---------------------------------------------------------------------------
# Section 2.4 — Operating efficiency (vòng quay tồn kho / phải thu)
# ---------------------------------------------------------------------------
def test_operating_efficiency_improving_inventory(asset_fpt):
    history = [
        _snap("2025-Q4", date(2025, 12, 31), inventory_days=25.0, receivable_days=40.0),
        *[_snap(f"2025-Q{q}", date(2025, q * 3, 30)) for q in (3, 2, 1)],
        _snap("2024-Q4", date(2024, 12, 31), inventory_days=30.0, receivable_days=50.0),
    ]
    res = compute_micro_score(asset_fpt, history[0], None, [], history=history)
    sec = res.checklist_report["section_2_4"]
    assert sec["passed"] >= 2  # both improvements + AR < 60 days


# ---------------------------------------------------------------------------
# Composite: weights re-normalize when sub-scores are missing
# ---------------------------------------------------------------------------
def test_weights_renormalize_when_sub_scores_missing(asset_fpt):
    # Only fundamentals present → ratio_absolute fires; no history → most others n/a.
    fund = _snap("2025-Q4", date(2025, 12, 31), roa=0.10, roe=0.20, pe=15.0, pb=2.5)
    res = compute_micro_score(asset_fpt, fund, None, [], history=[fund])
    assert res.score is not None
    # Total weight used should still be valid (not zero).
    assert "ratio_absolute" in res.breakdown
    assert -1.0 <= res.score <= 1.0


def test_news_against_threshold(asset_fpt):
    """news_against=True when sentiment < -0.3."""
    from datetime import datetime

    from finance_bot.ai.prompt import NewsBrief
    bad_news = [
        NewsBrief(title=f"điều tra phạt vi phạm thua lỗ {i}", source="x",
                  published_at=datetime.utcnow(), summary="cảnh báo")
        for i in range(3)
    ]
    res = compute_micro_score(asset_fpt, None, None, bad_news)
    assert res.news_against is True


def test_ratio_vs_industry_uses_new_ratios(asset_fpt):
    """ev_ebitda / ps / roic / margins / de_ratio also drive the vs-industry sub-score."""
    fund = _snap("2025-Q4", date(2025, 12, 31),
                 roic=0.20, gross_margin=0.45, net_margin=0.18, ev_ebitda=6.0,
                 ps=2.0, de_ratio=0.3)
    industry = IndustryAverage(
        industry_code="ICB-9000", period="2025-Q4",
        roic_median=0.10, gross_margin_median=0.25, net_margin_median=0.10,
        ev_ebitda_median=12.0, ps_median=3.0, de_ratio_median=1.0,
    )
    res = compute_micro_score(asset_fpt, fund, industry, [], history=[fund])
    # Asset beats industry on every ratio → score must be positive.
    assert res.breakdown.get("ratio_vs_industry", 0.0) > 0.0
