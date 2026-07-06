from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Protocol, Union

import pandas as pd

from .providers.common import pct_fmt, serialize_kline
from .us_quality import (
    calculate_risk_penalty,
    clamp,
    conviction_for,
    rating_for,
    risk_gate_for,
    scenario_for_us,
    technical_metrics,
)


class AsiaMarketProvider(Protocol):
    def us_history(self, ticker: str, start: date, end: date) -> pd.DataFrame: ...

    def us_quote(self, ticker: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AsiaCandidate:
    ticker: str
    name: str
    market: str
    currency: str
    sector: str
    market_cap_bucket: str
    quality: float
    growth: float
    valuation: float
    moat: float
    catalyst: float
    thesis: str
    risks: list[str]


def load_asia_markets_config(path: Union[str, Path]) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw["universe"] = [
        AsiaCandidate(
            ticker=str(item["ticker"]).upper(),
            name=item["name"],
            market=item.get("market", "日本"),
            currency=item.get("currency", "JPY"),
            sector=item.get("sector", "未分类"),
            market_cap_bucket=item.get("market_cap_bucket", "未知"),
            quality=float(item.get("quality", 50)),
            growth=float(item.get("growth", 50)),
            valuation=float(item.get("valuation", 50)),
            moat=float(item.get("moat", 50)),
            catalyst=float(item.get("catalyst", 50)),
            thesis=item.get("thesis", ""),
            risks=list(item.get("risks", [])),
        )
        for item in raw.get("universe", [])
    ]
    return raw


def screen_asia_markets(
    universe: list[AsiaCandidate],
    provider: AsiaMarketProvider,
    *,
    report_date: date,
    top_n: int = 24,
    lookback_days: int = 260,
) -> dict[str, Any]:
    start = report_date - timedelta(days=lookback_days)
    rows = [score_asia_candidate(item, provider, start=start, end=report_date) for item in universe]
    rows.sort(key=lambda item: item["score"], reverse=True)
    return {
        "date": str(report_date),
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "top": rows[:top_n],
        "all": rows,
        "summary": summarize_asia_markets(rows),
        "method": {
            "summary": "日韩市场筛选结合公司质量、成长、估值、护城河、产业催化和趋势纪律。",
            "risk_note": "仅以娱乐和公开资料整理为主，不构成投资建议。",
        },
    }


def score_asia_candidate(item: AsiaCandidate, provider: AsiaMarketProvider, *, start: date, end: date) -> dict[str, Any]:
    hist = _safe_history(provider, item.ticker, start, end)
    quote = _safe_quote(provider, item.ticker)
    metrics = technical_metrics(hist, quote)

    quality_component = item.quality * 0.24
    growth_component = item.growth * 0.18
    valuation_component = item.valuation * 0.14
    moat_component = item.moat * 0.16
    catalyst_component = item.catalyst * 0.12
    trend_component = metrics["trend_score"] * 0.10
    earnings_momentum_component = metrics["earnings_momentum_score"] * 0.08
    penalty = calculate_risk_penalty(item, metrics)

    score = clamp(
        quality_component
        + growth_component
        + valuation_component
        + moat_component
        + catalyst_component
        + trend_component
        + earnings_momentum_component
        - penalty,
        0,
        100,
    )
    risk_gate = risk_gate_for(item, metrics, penalty)
    conviction = conviction_for(score, item, metrics, penalty, risk_gate)
    rating = rating_for(score, metrics, risk_gate, conviction)

    return {
        "candidate": asdict(item),
        "ticker": item.ticker,
        "name": item.name,
        "market": item.market,
        "currency": item.currency,
        "sector": item.sector,
        "market_cap_bucket": item.market_cap_bucket,
        "score": round(score, 2),
        "rating": rating,
        "conviction": round(conviction, 1),
        "risk_gate": risk_gate,
        "buy_zone": buy_zone_text(metrics, rating),
        "scenario": scenario_for_us(metrics, score, item),
        "thesis": item.thesis,
        "risks": item.risks,
        "metrics": metrics,
        "kline": serialize_kline(hist, limit=120),
        "factors": {
            "quality": round(quality_component, 2),
            "growth": round(growth_component, 2),
            "valuation": round(valuation_component, 2),
            "moat": round(moat_component, 2),
            "catalyst": round(catalyst_component, 2),
            "trend": round(trend_component, 2),
            "earnings_momentum": round(earnings_momentum_component, 2),
            "risk_penalty": round(penalty, 2),
        },
        "formatted": {
            "price": format_price(metrics["price"], item.currency),
            "day_pct": pct_fmt(metrics["day_pct"]),
            "return_20d": pct_fmt(metrics["return_20d"]),
            "return_60d": pct_fmt(metrics["return_60d"]),
            "volatility_60d": pct_fmt(metrics["volatility_60d"]),
            "drawdown_60d": pct_fmt(metrics["drawdown_60d"]),
            "ma20_distance": pct_fmt(metrics["ma20_distance"]),
        },
    }


def buy_zone_text(metrics: dict[str, Any], rating: str) -> str:
    ma20_distance = metrics.get("ma20_distance")
    day_pct = metrics.get("day_pct")
    if rating in {"Avoid", "Underperform"}:
        return "娱乐观察名单，不进入严肃交易候选。"
    if day_pct is not None and day_pct >= 7:
        return "单日涨幅过大，娱乐观察即可，不追高。"
    if ma20_distance is None:
        return "缺少均线数据，仅作为跨市场观察。"
    if ma20_distance > 10:
        return "偏离 20 日均线较远，等待冷却。"
    if ma20_distance < -6:
        return "趋势未修复，等待重新站回 20 日均线。"
    if rating == "Buy":
        return "质量、趋势和催化共振，作为高关注观察对象。"
    if rating == "Outperform":
        return "评分靠前，适合继续跟踪财报和汇率影响。"
    return "维持观察，等待趋势或催化更明确。"


def summarize_asia_markets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    markets: dict[str, int] = {}
    ratings: dict[str, int] = {"Buy": 0, "Outperform": 0, "Neutral": 0, "Underperform": 0, "Avoid": 0}
    for row in rows:
        markets[row["market"]] = markets.get(row["market"], 0) + 1
        ratings[row["rating"]] = ratings.get(row["rating"], 0) + 1
    return {"market_counts": markets, "rating_counts": ratings}


def format_price(value: Any, currency: str) -> str:
    if value is None:
        return "未知"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "未知"
    prefix = {"JPY": "¥", "KRW": "₩"}.get(currency, "")
    if currency in {"JPY", "KRW"}:
        return f"{prefix}{price:,.0f}"
    return f"{price:,.2f}"


def _safe_history(provider: AsiaMarketProvider, ticker: str, start: date, end: date) -> pd.DataFrame:
    try:
        return provider.us_history(ticker, start, end)
    except Exception:
        return pd.DataFrame()


def _safe_quote(provider: AsiaMarketProvider, ticker: str) -> dict[str, Any]:
    try:
        return provider.us_quote(ticker)
    except Exception:
        return {"ticker": ticker, "price": None, "pct": None}
