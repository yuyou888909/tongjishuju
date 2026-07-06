from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd


class SampleProvider:
    """Deterministic offline data for testing the report shape."""

    def __init__(self) -> None:
        self.rng = np.random.default_rng(7)

    def us_quote(self, ticker: str) -> dict[str, Any]:
        pct_map = {
            "NVDA": 4.2,
            "AMD": 2.1,
            "AVGO": 3.4,
            "MU": 1.8,
            "TSLA": 2.6,
            "PDD": 1.7,
            "LLY": 1.2,
            "FCX": 2.4,
            "HOOD": 3.1,
            "LMT": -0.2,
        }
        pct = pct_map.get(ticker.upper(), self._stable_pct(ticker))
        return {"ticker": ticker.upper(), "name": ticker.upper(), "price": 100.0, "pct": pct, "market_cap": None}

    def us_history(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        return self._series(start, end, drift=self._stable_pct(ticker) / 250, scale=1.8, seed=ticker)

    def a_snapshot(self, code: str) -> dict[str, Any]:
        base = int(code[-3:]) if code[-3:].isdigit() else 100
        return {
            "code": code,
            "name": "",
            "price": 10 + base / 80,
            "pct": (base % 9) - 3,
            "market_cap": (base + 80) * 100_000_000,
            "float_market_cap": (base + 60) * 70_000_000,
            "turnover_rate": 1 + (base % 7),
            "volume_ratio": 0.8 + (base % 5) * 0.2,
            "pe": 20 + (base % 40),
        }

    def a_history(self, code: str, start: date, end: date) -> pd.DataFrame:
        drift = ((int(code[-2:]) if code[-2:].isdigit() else 20) - 35) / 3000
        return self._series(start, end, drift=drift, scale=2.3, seed=code)

    def stock_news(self, code: str, name: str = "", limit: int = 5) -> list[dict[str, str]]:
        titles = [
            f"{name or code} 获得 AI 数据中心订单，机构称产业链需求延续",
            f"{name or code} 发布业绩预告，收入保持增长",
            f"{name or code} 与头部客户签署战略合作协议",
            f"{name or code} 近期接受机构调研，关注产能释放节奏",
            f"{name or code} 公告回购计划并提示市场波动风险",
        ]
        today = date.today()
        return [
            {
                "title": title,
                "time": str(today - timedelta(days=i)),
                "source": "样例源",
                "url": "",
            }
            for i, title in enumerate(titles[:limit])
        ]

    def _series(self, start: date, end: date, drift: float, scale: float, seed: str) -> pd.DataFrame:
        dates = pd.bdate_range(start=start, end=end)
        local = np.random.default_rng(abs(hash(seed)) % (2**32))
        pct = local.normal(loc=drift * 100, scale=scale, size=len(dates))
        close = 100 * np.cumprod(1 + pct / 100)
        open_ = close / (1 + pct / 100)
        spread = np.abs(local.normal(loc=0.8, scale=0.35, size=len(dates)))
        high = np.maximum(open_, close) * (1 + spread / 100)
        low = np.minimum(open_, close) * (1 - spread / 100)
        volume = local.integers(800_000, 8_000_000, size=len(dates))
        return pd.DataFrame({"date": dates.normalize(), "open": open_, "high": high, "low": low, "close": close, "pct": pct, "volume": volume})

    def _stable_pct(self, key: str) -> float:
        return ((abs(hash(key)) % 700) / 100) - 2.5
