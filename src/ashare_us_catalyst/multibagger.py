from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Optional, Protocol, Union

import numpy as np
import pandas as pd

from .config import Theme
from .providers.common import format_money, pct_fmt, serialize_kline, to_number
from .scoring import clamp, current_theme_signal, score_news


class MultibaggerProvider(Protocol):
    def us_quote(self, ticker: str) -> dict[str, Any]: ...

    def us_history(self, ticker: str, start: date, end: date) -> pd.DataFrame: ...

    def a_snapshot(self, code: str) -> dict[str, Any]: ...

    def a_history(self, code: str, start: date, end: date) -> pd.DataFrame: ...

    def stock_news(self, code: str, name: str = "", limit: int = 5) -> list[dict[str, str]]: ...


@dataclass(frozen=True)
class EarlyUsCandidate:
    ticker: str
    name: str
    sector: str
    market_cap_bucket: str
    stage: str
    quality: float
    growth: float
    valuation: float
    moat: float
    catalyst: float
    thesis: str
    risks: list[str]


OPTIONALITY_WORDS = [
    "AI",
    "算力",
    "半导体",
    "光模块",
    "CPO",
    "机器人",
    "人形",
    "自动化",
    "卫星",
    "航天",
    "无人机",
    "创新药",
    "减重",
    "基因",
    "CXO",
    "储能",
    "锂",
    "固态",
    "核",
    "量子",
    "出海",
    "数据",
]

EARLY_NEWS_WORDS = [
    "机构调研",
    "订单",
    "中标",
    "量产",
    "获批",
    "合作",
    "突破",
    "出海",
    "产能",
    "商业化",
    "试点",
    "政策",
    "回购",
    "预增",
]


def load_multibagger_config(path: Union[str, Path]) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw["us_universe"] = [
        EarlyUsCandidate(
            ticker=str(item["ticker"]).upper(),
            name=item["name"],
            sector=item.get("sector", "未分类"),
            market_cap_bucket=item.get("market_cap_bucket", "未知"),
            stage=item.get("stage", "早期验证"),
            quality=float(item.get("quality", 50)),
            growth=float(item.get("growth", 50)),
            valuation=float(item.get("valuation", 50)),
            moat=float(item.get("moat", 50)),
            catalyst=float(item.get("catalyst", 50)),
            thesis=item.get("thesis", ""),
            risks=list(item.get("risks", [])),
        )
        for item in raw.get("us_universe", [])
    ]
    return raw


def discover_multibaggers(
    themes: list[Theme],
    us_universe: list[EarlyUsCandidate],
    provider: MultibaggerProvider,
    *,
    report_date: date,
    top_n: int = 24,
    lookback_days: int = 360,
    skip_news: bool = False,
) -> dict[str, Any]:
    start = report_date - timedelta(days=lookback_days)
    ashare_rows = score_ashare_candidates(themes, provider, start=start, end=report_date, skip_news=skip_news)
    us_rows = score_us_candidates(us_universe, provider, start=start, end=report_date)
    ashare_rows.sort(key=lambda item: item["score"], reverse=True)
    us_rows.sort(key=lambda item: item["score"], reverse=True)
    combined = sorted(
        [{"market": "A股", **row} for row in ashare_rows[:top_n]] + [{"market": "美股", **row} for row in us_rows[:top_n]],
        key=lambda item: item["score"],
        reverse=True,
    )
    return {
        "date": str(report_date),
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "method": {
            "summary": "寻找早期高倍潜力：产业空间、阶段位置、市值甜点、趋势确认、新闻催化和风险闸门共同评分。",
            "not_buy_signal": "候选清单只用于研究和跟踪，不是收益承诺或无条件买入指令。",
            "early_bias": "更偏好已经出现资金确认、但尚未完全透支的 0->1 或 1->10 阶段。",
        },
        "summary": summarize_multibaggers(ashare_rows, us_rows),
        "ashare": {"top": ashare_rows[:top_n], "all": ashare_rows},
        "us": {"top": us_rows[:top_n], "all": us_rows},
        "combined": combined[:top_n],
    }


def score_ashare_candidates(
    themes: list[Theme],
    provider: MultibaggerProvider,
    *,
    start: date,
    end: date,
    skip_news: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for theme in themes:
        signal = current_theme_signal(theme, provider)
        for candidate in theme.candidates:
            if candidate.code in seen:
                continue
            seen.add(candidate.code)
            snapshot = _safe_call(lambda c=candidate: provider.a_snapshot(c.code), {})
            hist = _safe_call(lambda c=candidate: provider.a_history(c.code, start, end), pd.DataFrame())
            news = [] if skip_news else _safe_call(lambda c=candidate: provider.stock_news(c.code, c.name, 6), [])
            metrics = technical_metrics(hist, snapshot)
            news_points, news_hits, news_risks = enhanced_news_score(news)
            optionality = optionality_score(" ".join([theme.name, candidate.industry, candidate.rationale]))
            stage = stage_for(metrics)
            market_cap_score = market_cap_sweet_spot(to_number(snapshot.get("market_cap")), market="a")
            trend = early_trend_score(metrics)
            accumulation = accumulation_score(metrics, snapshot)
            catalyst = clamp(signal.get("score", 50), 0, 100)
            risk_points = ashare_risk_points(snapshot, metrics, news_risks)
            risk_gate = risk_gate_from_points(risk_points)
            score = clamp(
                market_cap_score * 0.18
                + optionality * 0.18
                + catalyst * 0.18
                + trend * 0.18
                + accumulation * 0.12
                + clamp(50 + news_points * 9, 0, 100) * 0.10
                + max(0, 100 - risk_points * 5) * 0.06
                - max(0, risk_points - 7) * 1.4,
                0,
                100,
            )
            rating = opportunity_rating(score, risk_gate)
            row = {
                "kind": "ashare",
                "code": candidate.code,
                "ticker": candidate.code,
                "name": snapshot.get("name") or candidate.name,
                "industry": snapshot.get("industry") or candidate.industry or "未知",
                "theme": theme.name,
                "stage": stage,
                "score": round(score, 2),
                "rating": rating,
                "risk_gate": risk_gate,
                "conviction": round(conviction_from(score, risk_gate, metrics), 1),
                "market_cap": to_number(snapshot.get("market_cap")),
                "market_cap_bucket": format_money(snapshot.get("market_cap")),
                "thesis": multibagger_thesis(theme.name, candidate.industry, candidate.rationale, stage, signal),
                "trigger_conditions": trigger_conditions(snapshot, metrics, rating),
                "invalid_conditions": invalid_conditions(metrics, news_risks),
                "news": news,
                "news_hits": news_hits,
                "news_risks": news_risks,
                "metrics": metrics,
                "kline": serialize_kline(hist, limit=120),
                "factors": {
                    "market_cap_sweet_spot": round(market_cap_score, 1),
                    "industry_optionality": round(optionality, 1),
                    "catalyst": round(catalyst, 1),
                    "trend_confirmation": round(trend, 1),
                    "accumulation": round(accumulation, 1),
                    "news": round(clamp(50 + news_points * 9, 0, 100), 1),
                    "risk_control": round(max(0, 100 - risk_points * 5), 1),
                },
                "formatted": formatted_row(candidate.code, snapshot.get("name") or candidate.name, snapshot.get("market_cap"), metrics),
            }
            rows.append(row)
    return rows


def score_us_candidates(
    universe: list[EarlyUsCandidate],
    provider: MultibaggerProvider,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in universe:
        hist = _safe_call(lambda x=item: provider.us_history(x.ticker, start, end), pd.DataFrame())
        quote = _safe_call(lambda x=item: provider.us_quote(x.ticker), {"ticker": item.ticker})
        metrics = technical_metrics(hist, quote)
        market_cap_score = market_cap_sweet_spot(None, market="us", bucket=item.market_cap_bucket)
        optionality = optionality_score(" ".join([item.sector, item.stage, item.thesis]))
        trend = early_trend_score(metrics)
        accumulation = accumulation_score(metrics, quote)
        catalyst = clamp(item.catalyst, 0, 100)
        fundamentals = clamp(item.quality * 0.24 + item.growth * 0.28 + item.moat * 0.18 + item.valuation * 0.12 + catalyst * 0.18, 0, 100)
        risk_points = us_risk_points(item, metrics)
        risk_gate = risk_gate_from_points(risk_points)
        score = clamp(
            market_cap_score * 0.17
            + optionality * 0.17
            + fundamentals * 0.22
            + trend * 0.18
            + accumulation * 0.12
            + catalyst * 0.10
            + max(0, 100 - risk_points * 5) * 0.04
            - max(0, risk_points - 7) * 1.4,
            0,
            100,
        )
        rating = opportunity_rating(score, risk_gate)
        rows.append(
            {
                "kind": "us",
                "ticker": item.ticker,
                "code": item.ticker,
                "name": item.name,
                "industry": item.sector,
                "theme": item.sector,
                "stage": item.stage,
                "score": round(score, 2),
                "rating": rating,
                "risk_gate": risk_gate,
                "conviction": round(conviction_from(score, risk_gate, metrics), 1),
                "market_cap": quote.get("market_cap"),
                "market_cap_bucket": item.market_cap_bucket,
                "thesis": item.thesis,
                "trigger_conditions": trigger_conditions(quote, metrics, rating),
                "invalid_conditions": invalid_conditions(metrics, item.risks),
                "news": [],
                "news_hits": [],
                "news_risks": item.risks[:4],
                "risks": item.risks,
                "metrics": metrics,
                "kline": serialize_kline(hist, limit=120),
                "factors": {
                    "market_cap_sweet_spot": round(market_cap_score, 1),
                    "industry_optionality": round(optionality, 1),
                    "fundamentals": round(fundamentals, 1),
                    "catalyst": round(catalyst, 1),
                    "trend_confirmation": round(trend, 1),
                    "accumulation": round(accumulation, 1),
                    "risk_control": round(max(0, 100 - risk_points * 5), 1),
                },
                "formatted": formatted_row(item.ticker, item.name, item.market_cap_bucket, metrics, us=True),
            }
        )
    return rows


def technical_metrics(hist: pd.DataFrame, quote: dict[str, Any]) -> dict[str, Any]:
    price = to_number(quote.get("price"))
    day_pct = to_number(quote.get("pct"))
    turnover = to_number(quote.get("turnover_rate"))
    volume_ratio = to_number(quote.get("volume_ratio"))
    if hist is None or hist.empty:
        return {
            "price": price,
            "day_pct": day_pct,
            "return_20d": None,
            "return_60d": None,
            "return_120d": None,
            "volatility_60d": None,
            "drawdown_120d": None,
            "ma20_distance": None,
            "turnover_rate": turnover,
            "volume_ratio": volume_ratio,
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
    ma20 = float(closes.tail(20).mean()) if len(closes) >= 8 else latest
    high120 = float(closes.tail(120).max()) if len(closes) >= 8 else latest
    turnover_from_hist = None
    if "turnover_rate" in df.columns and pd.to_numeric(df["turnover_rate"], errors="coerce").notna().any():
        turnover_from_hist = float(pd.to_numeric(df["turnover_rate"], errors="coerce").dropna().tail(5).mean())
    return {
        "price": price,
        "day_pct": day_pct,
        "return_20d": period_return(closes, 20),
        "return_60d": period_return(closes, 60),
        "return_120d": period_return(closes, 120),
        "volatility_60d": float(pcts.tail(60).std() * np.sqrt(252)) if len(pcts.tail(60)) >= 10 else None,
        "drawdown_120d": ((latest / high120) - 1) * 100 if high120 else None,
        "ma20_distance": ((latest / ma20) - 1) * 100 if ma20 else None,
        "turnover_rate": turnover or turnover_from_hist,
        "volume_ratio": volume_ratio,
        "sparkline": [round(float(x), 2) for x in closes.tail(36)],
    }


def period_return(closes: pd.Series, window: int) -> Optional[float]:
    if len(closes) <= window:
        return None
    start = float(closes.iloc[-window - 1])
    end = float(closes.iloc[-1])
    if not start:
        return None
    return (end / start - 1) * 100


def market_cap_sweet_spot(value: Any, *, market: str, bucket: str = "") -> float:
    if market == "us":
        text = bucket.lower()
        if any(word in bucket for word in ["小盘", "中小"]) or "small" in text:
            return 88
        if any(word in bucket for word in ["中盘"]) or "mid" in text:
            return 82
        if any(word in bucket for word in ["大盘"]) or "large" in text:
            return 62
        if any(word in bucket for word in ["超大"]) or "mega" in text:
            return 38
        return 60
    market_cap = to_number(value)
    if market_cap is None:
        return 52
    billion = market_cap / 100_000_000
    if billion < 20:
        return 42
    if billion < 80:
        return 78
    if billion < 300:
        return 90
    if billion < 800:
        return 76
    if billion < 1800:
        return 58
    return 38


def optionality_score(text: str) -> float:
    score = 48.0
    upper = text.upper()
    for word in OPTIONALITY_WORDS:
        if word.upper() in upper:
            score += 6
    return clamp(score, 30, 96)


def enhanced_news_score(news: list[dict[str, str]]) -> tuple[float, list[str], list[str]]:
    base, hits, risks = score_news(news)
    titles = " ".join(item.get("title", "") for item in news)
    extra = sorted({word for word in EARLY_NEWS_WORDS if word in titles})
    merged_hits = sorted(set(hits) | set(extra))
    score = base + len(extra) * 0.45
    return score, merged_hits, risks


def early_trend_score(metrics: dict[str, Any]) -> float:
    score = 48.0
    ret20 = metrics.get("return_20d")
    ret60 = metrics.get("return_60d")
    ret120 = metrics.get("return_120d")
    ma20 = metrics.get("ma20_distance")
    drawdown = metrics.get("drawdown_120d")
    vol60 = metrics.get("volatility_60d")
    if ret20 is not None:
        score += clamp(ret20, -12, 28) * 0.7
    if ret60 is not None:
        score += clamp(ret60, -25, 70) * 0.32
    if ret120 is not None:
        score += clamp(ret120, -30, 120) * 0.12
        if ret120 > 160:
            score -= min(18, (ret120 - 160) * 0.10)
    if ma20 is not None:
        if -4 <= ma20 <= 10:
            score += 8
        elif ma20 > 18:
            score -= min(16, (ma20 - 18) * 0.8)
        elif ma20 < -12:
            score -= 10
    if drawdown is not None:
        if -18 <= drawdown <= -3:
            score += 6
        elif drawdown < -35:
            score -= 12
    if vol60 is not None and vol60 > 80:
        score -= min(12, (vol60 - 80) * 0.16)
    return clamp(score, 0, 100)


def accumulation_score(metrics: dict[str, Any], quote: dict[str, Any]) -> float:
    turnover = metrics.get("turnover_rate")
    volume_ratio = metrics.get("volume_ratio")
    day_pct = metrics.get("day_pct")
    ma20 = metrics.get("ma20_distance")
    score = 52.0
    if turnover is not None:
        if 1.5 <= turnover <= 9:
            score += 16
        elif turnover > 18:
            score -= 10
    if volume_ratio is not None:
        if 1.0 <= volume_ratio <= 2.5:
            score += 9
        elif volume_ratio > 4:
            score -= 8
    if day_pct is not None:
        if 0 <= day_pct <= 5:
            score += 8
        elif day_pct >= 8:
            score -= 12
    if ma20 is not None and -5 <= ma20 <= 8:
        score += 7
    return clamp(score, 0, 100)


def stage_for(metrics: dict[str, Any]) -> str:
    ret20 = metrics.get("return_20d")
    ret60 = metrics.get("return_60d")
    ret120 = metrics.get("return_120d")
    ma20 = metrics.get("ma20_distance")
    if ma20 is not None and ma20 < -10:
        return "等待趋势修复"
    if ret120 is not None and ret120 > 160:
        return "后期防追高"
    if ret60 is not None and ret60 > 24 and (ret120 is None or ret120 < 110):
        return "主升初段"
    if ret20 is not None and ret20 > 6 and (ret60 is None or ret60 < 30):
        return "启动确认"
    return "0->1验证期"


def ashare_risk_points(snapshot: dict[str, Any], metrics: dict[str, Any], news_risks: list[str]) -> float:
    risk = 0.0
    name = str(snapshot.get("name", ""))
    market_cap = to_number(snapshot.get("market_cap"))
    day_pct = metrics.get("day_pct")
    ma20 = metrics.get("ma20_distance")
    ret60 = metrics.get("return_60d")
    ret120 = metrics.get("return_120d")
    vol60 = metrics.get("volatility_60d")
    if "ST" in name.upper() or "退" in name:
        risk += 30
    if market_cap is not None and market_cap < 2_000_000_000:
        risk += 8
    if day_pct is not None and day_pct >= 8.5:
        risk += 8
    if ma20 is not None and ma20 >= 18:
        risk += 6
    if ret60 is not None and ret60 >= 90:
        risk += 6
    if ret120 is not None and ret120 >= 180:
        risk += 8
    if vol60 is not None and vol60 >= 95:
        risk += 6
    risk += min(10, len(news_risks) * 4)
    return risk


def us_risk_points(item: EarlyUsCandidate, metrics: dict[str, Any]) -> float:
    risk = 0.0
    ma20 = metrics.get("ma20_distance")
    day_pct = metrics.get("day_pct")
    ret60 = metrics.get("return_60d")
    ret120 = metrics.get("return_120d")
    vol60 = metrics.get("volatility_60d")
    if item.valuation < 35:
        risk += 6
    if item.quality < 45:
        risk += 4
    if day_pct is not None and day_pct >= 10:
        risk += 6
    if ma20 is not None and ma20 >= 22:
        risk += 7
    if ret60 is not None and ret60 >= 120:
        risk += 8
    if ret120 is not None and ret120 >= 220:
        risk += 8
    if vol60 is not None and vol60 >= 100:
        risk += 8
    risk += min(6, len(item.risks) * 1.2)
    return risk


def risk_gate_from_points(risk_points: float) -> str:
    if risk_points >= 14:
        return "Block"
    if risk_points >= 7:
        return "Watch"
    return "Pass"


def opportunity_rating(score: float, risk_gate: str) -> str:
    if risk_gate == "Block" or score < 48:
        return "Avoid"
    if score >= 76 and risk_gate == "Pass":
        return "Focus"
    if score >= 64:
        return "Watch"
    return "Track"


def conviction_from(score: float, risk_gate: str, metrics: dict[str, Any]) -> float:
    conviction = score
    if metrics.get("return_60d") is not None and 8 <= metrics["return_60d"] <= 70:
        conviction += 4
    if metrics.get("ma20_distance") is not None and -4 <= metrics["ma20_distance"] <= 10:
        conviction += 3
    if risk_gate == "Watch":
        conviction -= 9
    elif risk_gate == "Block":
        conviction -= 25
    return clamp(conviction, 0, 100)


def multibagger_thesis(theme: str, industry: str, rationale: str, stage: str, signal: dict[str, Any]) -> str:
    return (
        f"{theme} 中的 {industry} 候选，当前阶段为“{stage}”。"
        f"上游/海外信号：{signal.get('summary') or '暂无'}。"
        f"核心逻辑：{rationale}"
    )


def trigger_conditions(quote: dict[str, Any], metrics: dict[str, Any], rating: str) -> list[str]:
    price = metrics.get("price") or to_number(quote.get("price"))
    ma20 = metrics.get("ma20_distance")
    out = []
    if rating in {"Avoid"}:
        return ["不进入交易池，先等待风险闸门解除。"]
    if ma20 is not None and ma20 < -6:
        out.append("重新站回 20 日均线并维持放量。")
    elif ma20 is not None and ma20 > 14:
        out.append("等待回踩，避免在明显偏离均线时追价。")
    else:
        out.append("20 日趋势不破，成交活跃度维持或温和放大。")
    if price:
        out.append(f"观察 {price * 0.96:.2f}-{price * 1.03:.2f} 区间的承接强度。")
    out.append("新闻/公告继续出现订单、量产、获批、政策或业绩加速线索。")
    return out


def invalid_conditions(metrics: dict[str, Any], risks: list[str]) -> list[str]:
    out = [
        "跌破 20 日均线后无法快速收回，或 60 日趋势转弱。",
        "单日放量冲高回落且后续两日没有修复。",
    ]
    if risks:
        out.append("风险线索：" + "、".join(risks[:3]))
    else:
        out.append("出现减持、问询、业绩低于预期或核心订单落空。")
    if metrics.get("return_120d") is not None and metrics["return_120d"] > 160:
        out.append("120 日涨幅已过大，进入防追高模式。")
    return out


def formatted_row(symbol: str, name: str, market_cap: Any, metrics: dict[str, Any], *, us: bool = False) -> dict[str, str]:
    return {
        "symbol": symbol,
        "name": name,
        "market_cap": str(market_cap) if us and isinstance(market_cap, str) else format_money(market_cap),
        "price": f"${metrics['price']:.2f}" if us and metrics.get("price") is not None else (f"{metrics['price']:.2f}" if metrics.get("price") is not None else "未知"),
        "day_pct": pct_fmt(metrics.get("day_pct")),
        "return_20d": pct_fmt(metrics.get("return_20d")),
        "return_60d": pct_fmt(metrics.get("return_60d")),
        "return_120d": pct_fmt(metrics.get("return_120d")),
        "drawdown_120d": pct_fmt(metrics.get("drawdown_120d")),
        "ma20_distance": pct_fmt(metrics.get("ma20_distance")),
    }


def summarize_multibaggers(ashare_rows: list[dict[str, Any]], us_rows: list[dict[str, Any]]) -> dict[str, Any]:
    all_rows = ashare_rows + us_rows
    focus = [row for row in all_rows if row["rating"] == "Focus"]
    watch = [row for row in all_rows if row["rating"] == "Watch"]
    top = max(all_rows, key=lambda item: item["score"], default=None)
    stage_counts: dict[str, int] = {}
    for row in all_rows:
        stage_counts[row["stage"]] = stage_counts.get(row["stage"], 0) + 1
    return {
        "candidate_count": len(all_rows),
        "focus_count": len(focus),
        "watch_count": len(watch),
        "top_symbol": top["ticker"] if top else "",
        "top_name": top["name"] if top else "",
        "top_score": top["score"] if top else None,
        "stage_counts": sorted(stage_counts.items(), key=lambda item: item[1], reverse=True),
    }


def _safe_call(func, default):
    try:
        return func()
    except Exception:
        return default
