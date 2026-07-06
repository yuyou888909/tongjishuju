from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Optional, Protocol

import numpy as np
import pandas as pd

from .config import Candidate, Theme
from .providers.common import format_money, pct_fmt, serialize_kline, to_number


class MarketProvider(Protocol):
    def us_quote(self, ticker: str) -> dict[str, Any]: ...

    def us_history(self, ticker: str, start: date, end: date) -> pd.DataFrame: ...

    def a_snapshot(self, code: str) -> dict[str, Any]: ...

    def a_history(self, code: str, start: date, end: date) -> pd.DataFrame: ...

    def stock_news(self, code: str, name: str = "", limit: int = 5) -> list[dict[str, str]]: ...


POSITIVE_NEWS_WORDS = [
    "订单",
    "增长",
    "预增",
    "扭亏",
    "回购",
    "合作",
    "中标",
    "突破",
    "量产",
    "扩产",
    "涨价",
    "政策",
    "获批",
    "创新高",
    "机构调研",
]

NEGATIVE_NEWS_WORDS = [
    "减持",
    "亏损",
    "处罚",
    "问询",
    "立案",
    "下滑",
    "诉讼",
    "终止",
    "风险",
    "解禁",
    "质押",
]


def rank_report(
    themes: list[Theme],
    provider: MarketProvider,
    *,
    report_date: date,
    top_n: int = 5,
    lookback_days: int = 420,
    skip_news: bool = False,
    max_themes: Optional[int] = None,
) -> dict[str, Any]:
    start = report_date - timedelta(days=lookback_days)
    theme_blocks: list[dict[str, Any]] = []

    ranked_theme_inputs: list[tuple[Theme, Optional[dict[str, Any]]]]
    if max_themes:
        signal_pairs = [(theme, current_theme_signal(theme, provider)) for theme in themes]
        signal_pairs.sort(key=lambda item: item[1]["score"], reverse=True)
        ranked_theme_inputs = [(theme, signal) for theme, signal in signal_pairs[:max_themes]]
    else:
        ranked_theme_inputs = [(theme, None) for theme in themes]

    for theme, signal in ranked_theme_inputs:
        block = rank_theme(
            theme,
            provider,
            start=start,
            end=report_date,
            top_n=top_n,
            skip_news=skip_news,
            precomputed_signal=signal,
        )
        theme_blocks.append(block)

    theme_blocks.sort(key=lambda item: item["signal"]["score"], reverse=True)

    return {
        "date": str(report_date),
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "themes": theme_blocks,
    }


def rank_theme(
    theme: Theme,
    provider: MarketProvider,
    *,
    start: date,
    end: date,
    top_n: int,
    skip_news: bool,
    precomputed_signal: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    signal = precomputed_signal or current_theme_signal(theme, provider)
    theme_series = historical_theme_series(theme, provider, start, end)
    ranked: list[dict[str, Any]] = []

    for candidate in theme.candidates:
        snapshot = _safe_call(lambda: provider.a_snapshot(candidate.code), default={})
        hist = _safe_call(lambda: provider.a_history(candidate.code, start, end), default=pd.DataFrame())
        snapshot = enrich_snapshot_from_history(snapshot, hist)
        stats = historical_stats(hist, theme_series)
        news = [] if skip_news else _safe_call(lambda: provider.stock_news(candidate.code, candidate.name, 5), default=[])
        news_score, news_hits, news_risks = score_news(news)
        total, factors = score_candidate(signal, stats, snapshot, news_score, candidate)
        analyst = analyst_profile(total, signal, stats, snapshot, news_score, news_hits, news_risks, candidate)
        ranked.append(
            {
                "candidate": asdict(candidate),
                "snapshot": snapshot,
                "stats": stats,
                "news": news,
                "kline": serialize_kline(hist, limit=120),
                "news_hits": news_hits,
                "news_risks": news_risks,
                "score": round(total, 2),
                "factors": factors,
                "analyst": analyst,
                "formatted": format_candidate(candidate, snapshot, stats, news, total),
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {"theme": asdict(theme), "signal": signal, "top": ranked[:top_n], "all": ranked}


def current_theme_signal(theme: Theme, provider: MarketProvider) -> dict[str, Any]:
    quotes: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_sum = 0.0
    positive = 0
    for ticker in theme.us_tickers:
        quote = _safe_call(lambda t=ticker: provider.us_quote(t.ticker), default={"ticker": ticker.ticker, "pct": None})
        pct = to_number(quote.get("pct"))
        quote["weight"] = ticker.weight
        quote["configured_name"] = ticker.name
        quotes.append(quote)
        if pct is None:
            continue
        weighted_sum += pct * ticker.weight
        weight_sum += ticker.weight
        positive += 1 if pct > 0 else 0

    avg_pct = weighted_sum / weight_sum if weight_sum else 0.0
    breadth = positive / weight_sum if weight_sum else 0.0
    score = clamp(50 + avg_pct * 9 + breadth * 10, 0, 100)
    return {
        "avg_pct": round(avg_pct, 3),
        "score": round(score, 2),
        "breadth": round(breadth, 3),
        "quotes": quotes,
        "summary": _signal_summary(quotes),
    }


def historical_theme_series(theme: Theme, provider: MarketProvider, start: date, end: date) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for ticker in theme.us_tickers:
        hist = _safe_call(lambda t=ticker: provider.us_history(t.ticker, start, end), default=pd.DataFrame())
        if hist is None or hist.empty:
            continue
        frame = hist[["date", "pct"]].copy()
        frame["weighted_pct"] = pd.to_numeric(frame["pct"], errors="coerce") * ticker.weight
        frame["weight"] = ticker.weight
        frames.append(frame[["date", "weighted_pct", "weight"]])
    if not frames:
        return pd.DataFrame(columns=["date", "theme_pct"])
    raw = pd.concat(frames, ignore_index=True)
    grouped = raw.groupby("date", as_index=False).agg(weighted_pct=("weighted_pct", "sum"), weight=("weight", "sum"))
    grouped["theme_pct"] = grouped["weighted_pct"] / grouped["weight"].replace(0, np.nan)
    return grouped[["date", "theme_pct"]].dropna().sort_values("date")


def historical_stats(a_hist: pd.DataFrame, theme_series: pd.DataFrame, signal_threshold: float = 1.0) -> dict[str, Any]:
    if a_hist is None or a_hist.empty or theme_series is None or theme_series.empty:
        return _empty_stats()
    a = a_hist[["date", "pct"]].dropna().copy().sort_values("date")
    us = theme_series[["date", "theme_pct"]].dropna().copy().sort_values("date")
    if a.empty or us.empty:
        return _empty_stats()

    # A-share date C can react to the latest completed US session before C.
    a["signal_cutoff"] = a["date"] - pd.Timedelta(days=1)
    merged = pd.merge_asof(a, us, left_on="signal_cutoff", right_on="date", direction="backward", suffixes=("_a", "_us"))
    merged = merged.dropna(subset=["pct", "theme_pct"])
    if merged.empty:
        return _empty_stats()

    signal_days = merged[merged["theme_pct"] >= signal_threshold]
    positive_days = merged[merged["theme_pct"] > 0]
    corr = merged["pct"].corr(merged["theme_pct"]) if len(merged) >= 8 else np.nan
    var = merged["theme_pct"].var()
    beta = merged["pct"].cov(merged["theme_pct"]) / var if var and not np.isnan(var) else np.nan
    hit_rate = (signal_days["pct"] > 0).mean() if not signal_days.empty else np.nan
    avg_after_signal = signal_days["pct"].mean() if not signal_days.empty else np.nan
    avg_after_positive = positive_days["pct"].mean() if not positive_days.empty else np.nan
    return {
        "samples": int(len(merged)),
        "signal_samples": int(len(signal_days)),
        "hit_rate": _float_or_none(hit_rate),
        "avg_after_signal": _float_or_none(avg_after_signal),
        "avg_after_positive": _float_or_none(avg_after_positive),
        "corr": _float_or_none(corr),
        "beta": _float_or_none(beta),
    }


def score_candidate(
    signal: dict[str, Any],
    stats: dict[str, Any],
    snapshot: dict[str, Any],
    news_score: float,
    candidate: Candidate,
) -> tuple[float, dict[str, float]]:
    signal_component = signal["score"] * 0.34

    hit_rate = stats.get("hit_rate")
    avg_after_signal = stats.get("avg_after_signal")
    corr = stats.get("corr")
    beta = stats.get("beta")
    history_raw = 50.0
    if hit_rate is not None:
        history_raw += (hit_rate - 0.5) * 70
    if avg_after_signal is not None:
        history_raw += avg_after_signal * 4
    if corr is not None:
        history_raw += corr * 15
    if beta is not None:
        history_raw += clamp(beta, -1.5, 1.8) * 5
    history_component = clamp(history_raw, 0, 100) * 0.32

    day_pct = to_number(snapshot.get("pct")) or 0.0
    turnover = to_number(snapshot.get("turnover_rate")) or 0.0
    momentum_raw = 50 + clamp(day_pct, -6, 6) * 3 + clamp(turnover, 0, 12)
    momentum_component = clamp(momentum_raw, 0, 100) * 0.12

    market_cap = to_number(snapshot.get("market_cap"))
    liquidity_raw = 55
    if market_cap:
        if market_cap >= 100_000_000_000:
            liquidity_raw += 12
        elif market_cap < 8_000_000_000:
            liquidity_raw -= 14
    liquidity_component = clamp(liquidity_raw, 0, 100) * 0.08

    news_component = clamp(50 + news_score * 10, 0, 100) * 0.10
    risk_penalty = risk_penalty_points(snapshot, candidate, news_score)

    total = signal_component + history_component + momentum_component + liquidity_component + news_component - risk_penalty
    factors = {
        "signal": round(signal_component, 2),
        "history": round(history_component, 2),
        "momentum": round(momentum_component, 2),
        "liquidity": round(liquidity_component, 2),
        "news": round(news_component, 2),
        "risk_penalty": round(risk_penalty, 2),
    }
    return clamp(total, 0, 100), factors


def analyst_profile(
    total: float,
    signal: dict[str, Any],
    stats: dict[str, Any],
    snapshot: dict[str, Any],
    news_score: float,
    news_hits: list[str],
    news_risks: list[str],
    candidate: Candidate,
) -> dict[str, Any]:
    catalyst = clamp(signal.get("score", 50), 0, 100)
    history_edge = history_edge_score(stats)
    quality_proxy = quality_proxy_score(snapshot)
    momentum = momentum_score(snapshot)
    news = clamp(50 + news_score * 10, 0, 100)
    risk_points = risk_penalty_points(snapshot, candidate, news_score)
    risk_gate = risk_gate_for(snapshot, risk_points, news_risks)
    conviction = conviction_score(catalyst, history_edge, quality_proxy, momentum, news, stats, risk_gate)
    rating = analyst_rating(total, conviction, risk_gate)
    entry_plan = entry_plan_text(snapshot, rating)
    scenario = scenario_for(snapshot, stats, signal)
    return {
        "rating": rating,
        "conviction": round(conviction, 1),
        "risk_gate": risk_gate,
        "entry_plan": entry_plan,
        "scenario": scenario,
        "dimensions": {
            "catalyst": round(catalyst, 1),
            "history_edge": round(history_edge, 1),
            "quality_proxy": round(quality_proxy, 1),
            "momentum": round(momentum, 1),
            "news": round(news, 1),
            "risk": round(max(0, 100 - risk_points * 5), 1),
        },
        "positive_flags": news_hits[:4],
        "risk_flags": news_risks[:4],
        "method": "Top-down catalyst + historical edge + liquidity/quality proxy + momentum + event/risk gate",
    }


def history_edge_score(stats: dict[str, Any]) -> float:
    if not stats or not stats.get("samples"):
        return 45.0
    score = 50.0
    hit_rate = stats.get("hit_rate")
    avg_after_signal = stats.get("avg_after_signal")
    corr = stats.get("corr")
    beta = stats.get("beta")
    samples = stats.get("signal_samples") or 0
    if hit_rate is not None:
        score += (hit_rate - 0.5) * 90
    if avg_after_signal is not None:
        score += avg_after_signal * 5
    if corr is not None:
        score += corr * 18
    if beta is not None:
        score += clamp(beta, -1.5, 1.8) * 4
    if samples < 20:
        score -= 8
    elif samples >= 60:
        score += 4
    return clamp(score, 0, 100)


def quality_proxy_score(snapshot: dict[str, Any]) -> float:
    market_cap = to_number(snapshot.get("market_cap"))
    turnover = to_number(snapshot.get("turnover_rate")) or 0
    pe = to_number(snapshot.get("pe"))
    score = 50.0
    if market_cap:
        if market_cap >= 200_000_000_000:
            score += 18
        elif market_cap >= 80_000_000_000:
            score += 12
        elif market_cap < 8_000_000_000:
            score -= 15
    if 1 <= turnover <= 8:
        score += 8
    elif turnover > 15:
        score -= 6
    if pe is not None:
        if 0 < pe < 45:
            score += 6
        elif pe > 120:
            score -= 8
    return clamp(score, 0, 100)


def momentum_score(snapshot: dict[str, Any]) -> float:
    day_pct = to_number(snapshot.get("pct")) or 0.0
    turnover = to_number(snapshot.get("turnover_rate")) or 0.0
    score = 50 + clamp(day_pct, -6, 6) * 4
    if 1.2 <= turnover <= 8:
        score += 8
    elif turnover > 15:
        score -= 8
    if day_pct >= 8.5:
        score -= 12
    return clamp(score, 0, 100)


def risk_gate_for(snapshot: dict[str, Any], risk_points: float, news_risks: list[str]) -> str:
    day_pct = to_number(snapshot.get("pct"))
    name = str(snapshot.get("name", ""))
    if "ST" in name.upper() or "退" in name:
        return "Block"
    if risk_points >= 12 or len(news_risks) >= 2:
        return "Block"
    if day_pct is not None and day_pct >= 8.5:
        return "Watch"
    if risk_points >= 5 or news_risks:
        return "Watch"
    return "Pass"


def conviction_score(
    catalyst: float,
    history_edge: float,
    quality_proxy: float,
    momentum: float,
    news: float,
    stats: dict[str, Any],
    risk_gate: str,
) -> float:
    score = catalyst * 0.26 + history_edge * 0.28 + quality_proxy * 0.16 + momentum * 0.15 + news * 0.15
    signal_samples = stats.get("signal_samples") or 0
    if signal_samples >= 60:
        score += 5
    elif signal_samples < 20:
        score -= 6
    if risk_gate == "Watch":
        score -= 8
    elif risk_gate == "Block":
        score -= 25
    return clamp(score, 0, 100)


def analyst_rating(total: float, conviction: float, risk_gate: str) -> str:
    if risk_gate == "Block":
        return "Avoid"
    if total >= 76 and conviction >= 70 and risk_gate == "Pass":
        return "Buy"
    if total >= 68 and conviction >= 62:
        return "Outperform"
    if total >= 58:
        return "Neutral"
    return "Underperform"


def entry_plan_text(snapshot: dict[str, Any], rating: str) -> str:
    price = to_number(snapshot.get("price"))
    day_pct = to_number(snapshot.get("pct"))
    if rating in {"Avoid", "Underperform"}:
        return "不进入交易池，等待风险解除或评分修复。"
    if day_pct is not None and day_pct >= 8.5:
        return "高开/急涨过大，等待回落确认，不追价。"
    if price:
        if rating == "Buy":
            return f"回踩至 {price * 0.97:.2f}-{price * 1.01:.2f} 分批，跌破 {price * 0.91:.2f} 复核。"
        return f"观察 {price * 0.95:.2f}-{price * 1.00:.2f} 区间，放量确认再参与。"
    return "等待开盘价、成交额和风险闸门确认。"


def scenario_for(snapshot: dict[str, Any], stats: dict[str, Any], signal: dict[str, Any]) -> dict[str, str]:
    avg = stats.get("avg_after_signal")
    signal_pct = signal.get("avg_pct")
    day_pct = to_number(snapshot.get("pct"))
    base = avg if avg is not None else (signal_pct or 0) * 0.35
    bull = base + max(0.8, abs(signal_pct or 0) * 0.4)
    bear = -max(1.0, abs(day_pct or 0) * 0.35, abs(base) * 0.45)
    return {
        "bull": f"若板块扩散且成交放大，1-3 日弹性约 {bull:+.2f}%。",
        "base": f"基准情景参考历史映射，期望约 {base:+.2f}%。",
        "bear": f"若高开回落或负面新闻发酵，回撤压力约 {bear:+.2f}%。",
    }


def score_news(news: list[dict[str, str]]) -> tuple[float, list[str], list[str]]:
    positive_hits: list[str] = []
    risk_hits: list[str] = []
    for item in news:
        title = item.get("title", "")
        for word in POSITIVE_NEWS_WORDS:
            if word in title:
                positive_hits.append(word)
        for word in NEGATIVE_NEWS_WORDS:
            if word in title:
                risk_hits.append(word)
    score = len(set(positive_hits)) * 0.8 - len(set(risk_hits)) * 1.1
    return score, sorted(set(positive_hits)), sorted(set(risk_hits))


def risk_penalty_points(snapshot: dict[str, Any], candidate: Candidate, news_score: float) -> float:
    penalty = 0.0
    name = snapshot.get("name") or candidate.name
    if "ST" in str(name).upper() or "退" in str(name):
        penalty += 30
    day_pct = to_number(snapshot.get("pct"))
    if day_pct is not None and day_pct >= 8.5:
        penalty += 10
    if news_score < -1:
        penalty += min(12, abs(news_score) * 2)
    return penalty


def enrich_snapshot_from_history(snapshot: dict[str, Any], hist: pd.DataFrame) -> dict[str, Any]:
    enriched = dict(snapshot or {})
    latest = None
    if hist is not None and not hist.empty:
        latest_frame = hist.dropna(subset=["close"]).sort_values("date")
        if not latest_frame.empty:
            latest = latest_frame.iloc[-1]

    if latest is not None:
        latest_close = to_number(latest.get("close"))
        latest_pct = to_number(latest.get("pct"))
        if to_number(enriched.get("price")) is None and latest_close is not None:
            enriched["price"] = latest_close
        if latest_pct is not None:
            enriched["last_kline_pct"] = latest_pct
            enriched["last_kline_date"] = pd.Timestamp(latest.get("date")).strftime("%Y-%m-%d")

    if to_number(enriched.get("pct")) is not None and "pct_source" not in enriched:
        enriched["pct_source"] = "snapshot"
    return enriched


def format_candidate(
    candidate: Candidate,
    snapshot: dict[str, Any],
    stats: dict[str, Any],
    news: list[dict[str, str]],
    score: float,
) -> dict[str, str]:
    news_titles = []
    for item in news[:3]:
        title = item.get("title", "")
        when = item.get("time", "")
        source = item.get("source", "")
        prefix = " / ".join(part for part in [when, source] if part)
        news_titles.append(f"{prefix}: {title}" if prefix else title)
    hit_rate = stats.get("hit_rate")
    avg = stats.get("avg_after_signal")
    pct_source = snapshot.get("pct_source")
    pct_asof = " ".join(str(part) for part in [snapshot.get("pct_date"), snapshot.get("snapshot_time")] if part)
    return {
        "code": candidate.code,
        "name": snapshot.get("name") or candidate.name,
        "industry": snapshot.get("industry") or candidate.industry or "未知",
        "market_cap": format_money(snapshot.get("market_cap")),
        "day_pct": pct_fmt(snapshot.get("pct")),
        "day_pct_source": pct_source_label(pct_source),
        "day_pct_asof": pct_asof,
        "price": price_fmt(snapshot.get("price")),
        "score": f"{score:.1f}",
        "hit_rate": f"{hit_rate * 100:.1f}%" if hit_rate is not None else "样本不足",
        "avg_after_signal": pct_fmt(avg),
        "rationale": candidate.rationale,
        "news": "；".join(news_titles) if news_titles else "暂无可用新闻",
    }


def pct_source_label(value: Any) -> str:
    if value == "eastmoney_snapshot":
        return "东财实时"
    if value == "sina_snapshot":
        return "新浪实时"
    if value == "snapshot":
        return "实时快照"
    if value == "latest_kline":
        return "最近K线"
    return ""


def price_fmt(value: Any) -> str:
    num = to_number(value)
    if num is None:
        return "未知"
    return f"{num:.2f}"


def _signal_summary(quotes: list[dict[str, Any]], limit: int = 5) -> str:
    available = [q for q in quotes if to_number(q.get("pct")) is not None]
    available.sort(key=lambda item: to_number(item.get("pct")) or 0, reverse=True)
    return "，".join(f"{q.get('ticker')} {pct_fmt(q.get('pct'))}" for q in available[:limit]) or "暂无可用美股信号"


def _empty_stats() -> dict[str, Any]:
    return {
        "samples": 0,
        "signal_samples": 0,
        "hit_rate": None,
        "avg_after_signal": None,
        "avg_after_positive": None,
        "corr": None,
        "beta": None,
    }


def _safe_call(func, default):
    try:
        return func()
    except Exception:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(out) else out
