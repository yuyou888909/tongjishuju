from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Optional, Protocol, Union

import numpy as np
import pandas as pd

from .providers.common import pct_fmt, serialize_kline, to_number


class UsHistoryProvider(Protocol):
    def us_history(self, ticker: str, start: date, end: date) -> pd.DataFrame: ...

    def us_quote(self, ticker: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class UsQualityCandidate:
    ticker: str
    name: str
    sector: str
    market_cap_bucket: str
    quality: float
    growth: float
    valuation: float
    moat: float
    catalyst: float
    thesis: str
    risks: list[str]


def load_us_quality_config(path: Union[str, Path]) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw["universe"] = [
        UsQualityCandidate(
            ticker=str(item["ticker"]).upper(),
            name=item["name"],
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


def screen_us_quality(
    universe: list[UsQualityCandidate],
    provider: UsHistoryProvider,
    *,
    report_date: date,
    top_n: int = 20,
    lookback_days: int = 260,
) -> dict[str, Any]:
    start = report_date - timedelta(days=lookback_days)
    rows = [score_us_candidate(item, provider, start=start, end=report_date) for item in universe]
    rows.sort(key=lambda item: item["score"], reverse=True)
    return {
        "date": str(report_date),
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "top": rows[:top_n],
        "all": rows,
        "summary": summarize_us_quality(rows),
    }


def score_us_candidate(
    item: UsQualityCandidate,
    provider: UsHistoryProvider,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
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
    buy_zone = buy_zone_text(metrics, rating)
    scenario = scenario_for_us(metrics, score, item)

    return {
        "candidate": asdict(item),
        "ticker": item.ticker,
        "name": item.name,
        "sector": item.sector,
        "market_cap_bucket": item.market_cap_bucket,
        "score": round(score, 2),
        "rating": rating,
        "conviction": round(conviction, 1),
        "risk_gate": risk_gate,
        "buy_zone": buy_zone,
        "scenario": scenario,
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
            "price": f"${metrics['price']:.2f}" if metrics["price"] is not None else "未知",
            "day_pct": pct_fmt(metrics["day_pct"]),
            "return_20d": pct_fmt(metrics["return_20d"]),
            "return_60d": pct_fmt(metrics["return_60d"]),
            "volatility_60d": pct_fmt(metrics["volatility_60d"]),
            "drawdown_60d": pct_fmt(metrics["drawdown_60d"]),
            "ma20_distance": pct_fmt(metrics["ma20_distance"]),
        },
    }


def technical_metrics(hist: pd.DataFrame, quote: dict[str, Any]) -> dict[str, Any]:
    price = to_number(quote.get("price"))
    day_pct = to_number(quote.get("pct"))

    if hist is None or hist.empty:
        return {
            "price": price,
            "day_pct": day_pct,
            "return_20d": None,
            "return_60d": None,
            "volatility_60d": None,
            "drawdown_60d": None,
            "ma20_distance": None,
            "trend_score": 50.0,
            "earnings_momentum_score": 50.0,
            "sparkline": [],
        }

    df = hist.dropna(subset=["close"]).sort_values("date").copy()
    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    pcts = pd.to_numeric(df["pct"], errors="coerce").dropna()
    if closes.empty:
        return technical_metrics(pd.DataFrame(), quote)

    latest = float(closes.iloc[-1])
    price = price or latest
    if day_pct is None and not pcts.empty:
        day_pct = float(pcts.iloc[-1])
    ret20 = _period_return(closes, 20)
    ret60 = _period_return(closes, 60)
    ret120 = _period_return(closes, 120)
    vol60 = float(pcts.tail(60).std() * np.sqrt(252)) if len(pcts.tail(60)) >= 10 else None
    rolling_high = float(closes.tail(60).max()) if len(closes) >= 2 else latest
    drawdown = ((latest / rolling_high) - 1) * 100 if rolling_high else None
    ma20 = float(closes.tail(20).mean()) if len(closes) >= 5 else latest
    ma20_distance = ((latest / ma20) - 1) * 100 if ma20 else None

    trend_score = 50.0
    if ret20 is not None:
        trend_score += clamp(ret20, -15, 18) * 1.1
    if ret60 is not None:
        trend_score += clamp(ret60, -25, 35) * 0.55
    if ma20_distance is not None:
        trend_score -= max(0, ma20_distance - 9) * 1.3
    if vol60 is not None:
        trend_score -= max(0, vol60 - 48) * 0.25
    if drawdown is not None:
        trend_score += clamp(drawdown, -25, 0) * 0.25
    trend_score = clamp(trend_score, 0, 100)
    earnings_momentum_score = 50.0
    if ret20 is not None:
        earnings_momentum_score += clamp(ret20, -12, 18) * 0.9
    if ret60 is not None:
        earnings_momentum_score += clamp(ret60, -20, 35) * 0.45
    if ret120 is not None:
        earnings_momentum_score += clamp(ret120, -25, 55) * 0.2
    if vol60 is not None and vol60 > 55:
        earnings_momentum_score -= (vol60 - 55) * 0.25
    earnings_momentum_score = clamp(earnings_momentum_score, 0, 100)

    spark = [round(float(x), 2) for x in closes.tail(36)]
    return {
        "price": price,
        "day_pct": day_pct,
        "return_20d": ret20,
        "return_60d": ret60,
        "return_120d": ret120,
        "volatility_60d": vol60,
        "drawdown_60d": drawdown,
        "ma20_distance": ma20_distance,
        "trend_score": trend_score,
        "earnings_momentum_score": earnings_momentum_score,
        "sparkline": spark,
    }


def calculate_risk_penalty(item: UsQualityCandidate, metrics: dict[str, Any]) -> float:
    penalty = 0.0
    day_pct = metrics.get("day_pct")
    ma20_distance = metrics.get("ma20_distance")
    vol60 = metrics.get("volatility_60d")

    if item.valuation < 45:
        penalty += 6
    if day_pct is not None and day_pct >= 7:
        penalty += 5
    if ma20_distance is not None and ma20_distance >= 14:
        penalty += 8
    if vol60 is not None and vol60 >= 65:
        penalty += 6
    if len(item.risks) >= 4:
        penalty += 2
    return penalty


def risk_gate_for(item: UsQualityCandidate, metrics: dict[str, Any], penalty: float) -> str:
    ma20_distance = metrics.get("ma20_distance")
    day_pct = metrics.get("day_pct")
    vol60 = metrics.get("volatility_60d")
    if item.valuation < 40 and ma20_distance is not None and ma20_distance > 15:
        return "Block"
    if penalty >= 12 or (vol60 is not None and vol60 >= 75):
        return "Block"
    if day_pct is not None and day_pct >= 7:
        return "Watch"
    if ma20_distance is not None and ma20_distance >= 12:
        return "Watch"
    if penalty >= 6:
        return "Watch"
    return "Pass"


def conviction_for(score: float, item: UsQualityCandidate, metrics: dict[str, Any], penalty: float, risk_gate: str) -> float:
    conviction = score
    if item.quality >= 90 and item.moat >= 88:
        conviction += 4
    if item.valuation < 45:
        conviction -= 6
    if metrics.get("trend_score", 50) >= 72:
        conviction += 3
    if risk_gate == "Watch":
        conviction -= 8
    elif risk_gate == "Block":
        conviction -= 24
    conviction -= min(10, penalty * 0.5)
    return clamp(conviction, 0, 100)


def rating_for(score: float, metrics: dict[str, Any], risk_gate: str, conviction: float) -> str:
    ma20_distance = metrics.get("ma20_distance")
    if risk_gate == "Block":
        return "Avoid"
    if score >= 82 and conviction >= 76 and (ma20_distance is None or ma20_distance < 10):
        return "Buy"
    if score >= 74 and conviction >= 66:
        return "Outperform"
    if score >= 64:
        return "Neutral"
    return "Underperform"


def buy_zone_text(metrics: dict[str, Any], rating: str) -> str:
    ma20_distance = metrics.get("ma20_distance")
    day_pct = metrics.get("day_pct")
    if rating in {"Avoid", "Underperform"}:
        return "暂不进入候选池，等待质量/趋势或估值改善。"
    if day_pct is not None and day_pct >= 7:
        return "单日涨幅过大，倾向等待回落或下一次确认。"
    if ma20_distance is None:
        return "缺少均线数据，建议只作为基本面观察名单。"
    if ma20_distance > 10:
        return "价格偏离 20 日均线较远，优先等待回踩。"
    if ma20_distance < -6:
        return "低于 20 日均线，等待重新站回趋势线。"
    if rating == "Buy":
        return "趋势和基本面评分共振，可按分批和止损纪律纳入核心候选。"
    if rating == "Outperform":
        return "评分优于市场，适合等待回踩或确认突破后分批。"
    return "维持观察，等待估值、趋势或催化进一步改善。"


def scenario_for_us(metrics: dict[str, Any], score: float, item: UsQualityCandidate) -> dict[str, str]:
    ret20 = metrics.get("return_20d") or 0
    ret60 = metrics.get("return_60d") or 0
    vol60 = metrics.get("volatility_60d") or 35
    bull = clamp((score - 60) * 0.45 + max(0, ret20) * 0.25, 3, 25)
    base = clamp((score - 60) * 0.18 + ret60 * 0.08, -5, 16)
    bear = -clamp(vol60 * 0.18 + max(0, 50 - item.valuation) * 0.08, 5, 22)
    return {
        "bull": f"牛市情景：催化兑现且估值不压缩，3-8 周上行约 {bull:+.1f}%。",
        "base": f"基准情景：基本面和趋势正常延续，3-8 周期望约 {base:+.1f}%。",
        "bear": f"熊市情景：估值压缩/风险事件触发，需预设 {bear:+.1f}% 左右回撤容忍。",
    }


def summarize_us_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {"Buy": 0, "Outperform": 0, "Neutral": 0, "Underperform": 0, "Avoid": 0}
    sectors: dict[str, int] = {}
    for row in rows:
        counts[row["rating"]] = counts.get(row["rating"], 0) + 1
        sectors[row["sector"]] = sectors.get(row["sector"], 0) + 1
    top_sector = sorted(sectors.items(), key=lambda item: item[1], reverse=True)[:3]
    return {"rating_counts": counts, "top_sectors": top_sector}


def _period_return(closes: pd.Series, window: int) -> Optional[float]:
    if len(closes) <= window:
        return None
    start = float(closes.iloc[-window - 1])
    end = float(closes.iloc[-1])
    if not start:
        return None
    return (end / start - 1) * 100


def _safe_history(provider: UsHistoryProvider, ticker: str, start: date, end: date) -> pd.DataFrame:
    try:
        return provider.us_history(ticker, start, end)
    except Exception:
        return pd.DataFrame()


def _safe_quote(provider: UsHistoryProvider, ticker: str) -> dict[str, Any]:
    try:
        return provider.us_quote(ticker)
    except Exception:
        return {"ticker": ticker, "price": None, "pct": None}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
