from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math
import re
from typing import Any, Optional, Union

import pandas as pd


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def first_column(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    for name in names:
        if name in df.columns:
            return name
    lowered = {str(col).lower(): col for col in df.columns}
    for name in names:
        found = lowered.get(name.lower())
        if found is not None:
            return str(found)
    return None


def to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "--", "None", "nan", "NaN"}:
        return None
    multiplier = 1.0
    if text.endswith("万亿"):
        multiplier = 1_000_000_000_000
        text = text[:-2]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def format_money(value: Any) -> str:
    num = to_number(value)
    if num is None:
        return "未知"
    abs_num = abs(num)
    if abs_num >= 1_000_000_000_000:
        return f"{num / 1_000_000_000_000:.2f}万亿"
    if abs_num >= 100_000_000:
        return f"{num / 100_000_000:.1f}亿"
    if abs_num >= 10_000:
        return f"{num / 10_000:.1f}万"
    return f"{num:.0f}"


def pct_fmt(value: Any, default: str = "未知") -> str:
    num = to_number(value)
    if num is None:
        return default
    return f"{num:+.2f}%"


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "close", "pct"])
    date_col = first_column(df, ["日期", "date", "时间"])
    open_col = first_column(df, ["开盘", "open"])
    high_col = first_column(df, ["最高", "high"])
    low_col = first_column(df, ["最低", "low"])
    close_col = first_column(df, ["收盘", "close", "最新价"])
    pct_col = first_column(df, ["涨跌幅", "pct", "changePercent"])
    volume_col = first_column(df, ["成交量", "volume", "vol"])
    amount_col = first_column(df, ["成交额", "amount"])
    turnover_col = first_column(df, ["换手率", "turnover", "turnover_rate"])
    if date_col is None or close_col is None:
        return pd.DataFrame(columns=["date", "close", "pct"])
    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None).dt.normalize()
    out["close"] = pd.to_numeric(df[close_col], errors="coerce")
    if open_col is not None:
        out["open"] = pd.to_numeric(df[open_col], errors="coerce")
    if high_col is not None:
        out["high"] = pd.to_numeric(df[high_col], errors="coerce")
    if low_col is not None:
        out["low"] = pd.to_numeric(df[low_col], errors="coerce")
    if pct_col is not None:
        out["pct"] = pd.to_numeric(df[pct_col].astype(str).str.replace("%", "", regex=False), errors="coerce")
    else:
        out["pct"] = out["close"].pct_change() * 100
    if volume_col is not None:
        out["volume"] = pd.to_numeric(df[volume_col], errors="coerce")
    if amount_col is not None:
        out["amount"] = pd.to_numeric(df[amount_col], errors="coerce")
    if turnover_col is not None:
        out["turnover_rate"] = pd.to_numeric(df[turnover_col].astype(str).str.replace("%", "", regex=False), errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    out["pct"] = out["pct"].fillna(out["close"].pct_change() * 100)
    columns = ["date", "close", "pct"]
    for optional in ["open", "high", "low", "volume", "amount", "turnover_rate"]:
        if optional in out.columns:
            columns.append(optional)
    return out[columns]


def serialize_kline(df: pd.DataFrame, limit: int = 120) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    frame = df.sort_values("date").tail(limit)
    for _, row in frame.iterrows():
        item: dict[str, Any] = {
            "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            "close": _finite_float(row.get("close")),
            "pct": _finite_float(row.get("pct")),
        }
        for key in ["open", "high", "low", "volume", "amount", "turnover_rate"]:
            value = _finite_float(row.get(key))
            if value is not None:
                item[key] = value
        out.append(item)
    return out


def _finite_float(value: Any) -> Optional[float]:
    num = to_number(value)
    if num is None or math.isnan(num):
        return None
    return round(float(num), 4)


class CsvCache:
    def __init__(self, cache_dir: Union[str, Path], enabled: bool = True) -> None:
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.cache_dir / f"{safe_filename(name)}.csv"

    def get(self, name: str, max_age_hours: Optional[float] = None) -> Optional[pd.DataFrame]:
        if not self.enabled:
            return None
        path = self.path(name)
        if not path.exists():
            return None
        if max_age_hours is not None:
            age_hours = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 3600
            if age_hours > max_age_hours:
                return None
        try:
            return pd.read_csv(path)
        except Exception:
            return None

    def put(self, name: str, df: pd.DataFrame) -> None:
        if not self.enabled:
            return
        path = self.path(name)
        df.to_csv(path, index=False)
