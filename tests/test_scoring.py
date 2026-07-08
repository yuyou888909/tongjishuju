from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from ashare_us_catalyst.asia_markets import AsiaCandidate, screen_asia_markets
from ashare_us_catalyst.backtest import run_backtest
from ashare_us_catalyst.config import Candidate, Theme, UsTicker
from ashare_us_catalyst.multibagger import EarlyUsCandidate, discover_multibaggers
from ashare_us_catalyst.providers.sample_provider import SampleProvider
from ashare_us_catalyst.providers.akshare_provider import _eastmoney_quote_datetime, _sanitize_a_snapshot_quote
from ashare_us_catalyst.scoring import enrich_snapshot_from_history, format_candidate, historical_stats, score_news
from ashare_us_catalyst.us_quality import UsQualityCandidate
from ashare_us_catalyst.us_quality import technical_metrics


class ScoringTests(unittest.TestCase):
    def test_historical_stats_aligns_previous_us_session(self) -> None:
        a_hist = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
                "close": [10, 10.5, 10.2],
                "pct": [1.0, 2.0, -1.0],
            }
        )
        theme = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
                "theme_pct": [3.0, -2.0, 4.0],
            }
        )
        stats = historical_stats(a_hist, theme, signal_threshold=1.0)
        self.assertEqual(stats["samples"], 3)
        self.assertEqual(stats["signal_samples"], 2)
        self.assertAlmostEqual(stats["hit_rate"], 0.5)

    def test_score_news_positive_and_negative_words(self) -> None:
        score, hits, risks = score_news(
            [
                {"title": "公司获得大客户订单并发布回购计划"},
                {"title": "股东拟减持，公司提示风险"},
            ]
        )
        self.assertIn("订单", hits)
        self.assertIn("回购", hits)
        self.assertIn("减持", risks)
        self.assertLess(score, 2.0)

    def test_candidate_change_requires_realtime_snapshot(self) -> None:
        hist = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-06-30", "2026-07-01"]),
                "close": [10.0, 10.35],
                "pct": [0.4, 3.5],
            }
        )
        snapshot = enrich_snapshot_from_history({"code": "300308"}, hist)
        formatted = format_candidate(
            Candidate(code="300308", name="中际旭创", industry="光模块", rationale="AI optical module"),
            snapshot,
            {"hit_rate": 0.56, "avg_after_signal": 1.2},
            [],
            72.4,
        )
        self.assertEqual(formatted["day_pct"], "未知")
        self.assertEqual(formatted["day_pct_source"], "")
        self.assertEqual(formatted["price"], "10.35")

    def test_candidate_change_formats_realtime_snapshot(self) -> None:
        formatted = format_candidate(
            Candidate(code="300308", name="中际旭创", industry="光模块", rationale="AI optical module"),
            {"price": 123.45, "pct": 3.5, "pct_source": "sina_snapshot", "pct_date": "2026-07-03", "snapshot_time": "11:30:00"},
            {"hit_rate": 0.56, "avg_after_signal": 1.2},
            [],
            72.4,
        )
        self.assertEqual(formatted["day_pct"], "+3.50%")
        self.assertEqual(formatted["day_pct_source"], "新浪实时")
        self.assertEqual(formatted["day_pct_asof"], "2026-07-03 11:30:00")
        self.assertEqual(formatted["price"], "123.45")

    def test_zero_price_snapshot_is_not_negative_one_hundred_percent(self) -> None:
        snapshot = _sanitize_a_snapshot_quote(
            {
                "code": "300750",
                "name": "宁德时代",
                "prev_close": 200.0,
                "price": 0.0,
                "pct": -100.0,
                "pct_source": "sina_snapshot",
            }
        )
        self.assertIsNone(snapshot["price"])
        self.assertIsNone(snapshot["pct"])
        self.assertEqual(snapshot["pct_source"], "not_open")

    def test_eastmoney_quote_time_parses_compact_timestamp(self) -> None:
        parsed = _eastmoney_quote_datetime("20260709150304")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.strftime("%Y-%m-%d %H:%M:%S"), "2026-07-09 15:03:04")

    def test_us_quality_technical_metrics(self) -> None:
        hist = pd.DataFrame(
            {
                "date": pd.bdate_range("2026-01-01", periods=80),
                "close": [100 + i for i in range(80)],
                "pct": [0.5] * 80,
            }
        )
        metrics = technical_metrics(hist, {"price": 179, "pct": 0.5})
        self.assertGreater(metrics["return_20d"], 10)
        self.assertGreater(metrics["trend_score"], 50)

    def test_backtest_outputs_half_year_metrics(self) -> None:
        theme = Theme(
            id="ai",
            name="AI",
            logic="AI linkage",
            us_tickers=[UsTicker(ticker="NVDA", name="NVIDIA", weight=1.0)],
            candidates=[Candidate(code="300308", name="中际旭创", industry="光模块", rationale="AI optical module")],
        )
        us_candidate = UsQualityCandidate(
            ticker="NVDA",
            name="NVIDIA",
            sector="AI",
            market_cap_bucket="大盘",
            quality=90,
            growth=88,
            valuation=55,
            moat=90,
            catalyst=92,
            thesis="AI demand",
            risks=["估值波动"],
        )
        result = run_backtest([theme], [us_candidate], SampleProvider(), report_date=date(2026, 7, 1), window_days=90, lookback_days=120, top_n=1)
        self.assertGreater(result["ashare"]["summary"]["trade_count"], 0)
        self.assertGreater(result["us"]["summary"]["trade_count"], 0)
        self.assertIn("hit_rate_1d", result["ashare"]["summary"])

    def test_multibagger_outputs_cross_market_candidates(self) -> None:
        theme = Theme(
            id="robotics",
            name="机器人",
            logic="robotics linkage",
            us_tickers=[UsTicker(ticker="TSLA", name="Tesla", weight=1.0)],
            candidates=[Candidate(code="300024", name="机器人", industry="机器人系统集成", rationale="机器人主题弹性")],
        )
        us_candidate = EarlyUsCandidate(
            ticker="RKLB",
            name="Rocket Lab",
            sector="商业航天",
            market_cap_bucket="中小盘",
            stage="1->10放量期",
            quality=62,
            growth=88,
            valuation=42,
            moat=68,
            catalyst=84,
            thesis="商业航天早期放量",
            risks=["亏损公司"],
        )
        result = discover_multibaggers([theme], [us_candidate], SampleProvider(), report_date=date(2026, 7, 1), top_n=5, lookback_days=180)
        self.assertEqual(result["summary"]["candidate_count"], 2)
        self.assertTrue(result["combined"])
        self.assertIn(result["combined"][0]["rating"], {"Focus", "Watch", "Track", "Avoid"})

    def test_asia_markets_outputs_japan_korea_candidates(self) -> None:
        universe = [
            AsiaCandidate(
                ticker="7203.T",
                name="Toyota Motor",
                market="日本",
                currency="JPY",
                sector="汽车/混动",
                market_cap_bucket="超大盘",
                quality=88,
                growth=66,
                valuation=72,
                moat=86,
                catalyst=70,
                thesis="混动、汇率和全球供应链修复支撑观察价值。",
                risks=["汇率波动", "全球汽车需求放缓"],
            ),
            AsiaCandidate(
                ticker="005930.KS",
                name="Samsung Electronics",
                market="韩国",
                currency="KRW",
                sector="存储/半导体",
                market_cap_bucket="超大盘",
                quality=86,
                growth=74,
                valuation=70,
                moat=84,
                catalyst=78,
                thesis="存储周期、HBM 和 AI 服务器需求是核心变量。",
                risks=["存储周期", "韩元波动"],
            ),
        ]
        result = screen_asia_markets(universe, SampleProvider(), report_date=date(2026, 7, 1), top_n=2, lookback_days=120)
        markets = {row["market"] for row in result["top"]}
        self.assertEqual(markets, {"日本", "韩国"})
        self.assertIn("risk_note", result["method"])
        self.assertIn("不构成投资建议", result["method"]["risk_note"])


if __name__ == "__main__":
    unittest.main()
