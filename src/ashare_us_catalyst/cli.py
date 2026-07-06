from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
import sys
from typing import Optional

from .config import load_config
from .notifier import load_env, send_telegram
from .providers import AkshareProvider, SampleProvider
from .report import render_markdown, write_outputs
from .scoring import rank_report


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成美股映射 A 股早报")
    parser.add_argument("--config", default="config/themes.json", help="板块和候选池配置")
    parser.add_argument("--output", default="reports", help="报告输出目录")
    parser.add_argument("--cache", default=".cache", help="行情缓存目录")
    parser.add_argument("--top", type=int, default=None, help="每个板块输出股票数量")
    parser.add_argument("--lookback-days", type=int, default=None, help="历史回看天数")
    parser.add_argument("--max-themes", type=int, default=None, help="只输出信号最强的 N 个板块")
    parser.add_argument("--date", default=None, help="报告日期，格式 YYYY-MM-DD，默认今天")
    parser.add_argument("--sample-data", action="store_true", help="使用离线样例数据")
    parser.add_argument("--skip-news", action="store_true", help="不抓取个股新闻")
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存")
    parser.add_argument("--allow-akshare-fallback", action="store_true", help="直连接口失败时允许使用 AkShare 慢回退")
    parser.add_argument("--send-telegram", action="store_true", help="发送 Telegram 摘要")
    parser.add_argument("--env", default=".env", help="环境变量文件")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    report_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    top_n = args.top or int(config.report.get("default_top_n", 5))
    lookback_days = args.lookback_days or int(config.report.get("default_lookback_days", 420))

    provider = (
        SampleProvider()
        if args.sample_data
        else AkshareProvider(args.cache, use_cache=not args.no_cache, allow_akshare_fallback=args.allow_akshare_fallback)
    )
    result = rank_report(
        config.themes,
        provider,
        report_date=report_date,
        top_n=top_n,
        lookback_days=lookback_days,
        skip_news=args.skip_news,
        max_themes=args.max_themes,
    )
    markdown = render_markdown(result, config.report.get("risk_note", "本报告不构成投资建议。"))
    md_path, json_path = write_outputs(result, markdown, args.output)
    print(f"已生成报告: {Path(md_path).resolve()}")
    print(f"已生成结构化数据: {Path(json_path).resolve()}")

    if args.send_telegram:
        load_env(args.env)
        send_telegram(markdown)
        print("已发送 Telegram 摘要")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
