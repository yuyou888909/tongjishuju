from __future__ import annotations

from pathlib import Path
from typing import Any, Tuple, Union
import json

from .providers.common import pct_fmt


def render_markdown(result: dict[str, Any], risk_note: str) -> str:
    lines: list[str] = []
    lines.append(f"# 美股映射 A 股早报 - {result['date']}")
    lines.append("")
    lines.append(f"> {risk_note}")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    for block in result["themes"][:6]:
        signal = block["signal"]
        theme = block["theme"]
        lines.append(f"- {theme['name']}：美股主题均值 {pct_fmt(signal['avg_pct'])}，信号分 {signal['score']:.1f}；{signal['summary']}")
    lines.append("")

    for block in result["themes"]:
        theme = block["theme"]
        signal = block["signal"]
        lines.append(f"## {theme['name']}")
        lines.append("")
        lines.append(f"美股触发：{signal['summary']}。主题信号均值 {pct_fmt(signal['avg_pct'])}，信号分 {signal['score']:.1f}。")
        lines.append("")
        lines.append(f"映射逻辑：{theme['logic']}")
        lines.append("")
        lines.append(
            "| 排名 | 代码 | 名称 | 评级 | Conviction | Risk Gate | 产业 | 市值 | 当日涨跌 | 综合分 | Entry Plan | 利好逻辑 | 近期重大新闻 |"
        )
        lines.append("| --- | --- | --- | --- | ---: | --- | --- | --- | --- | ---: | --- | --- | --- |")
        for idx, item in enumerate(block["top"], start=1):
            f = item["formatted"]
            analyst = item.get("analyst", {})
            lines.append(
                f"| {idx} | {f['code']} | {f['name']} | {analyst.get('rating', 'Neutral')} | "
                f"{analyst.get('conviction', '-')} | {analyst.get('risk_gate', 'Watch')} | {f['industry']} | {f['market_cap']} | "
                f"{_change_cell(f)} | {f['score']} | {_cell(analyst.get('entry_plan', f['rationale']))} | "
                f"{_cell(f['rationale'])} | {_cell(f['news'])} |"
            )
        lines.append("")
        lines.append("分析师评分拆解：")
        for idx, item in enumerate(block["top"], start=1):
            f = item["formatted"]
            factors = item["factors"]
            analyst = item.get("analyst", {})
            dimensions = analyst.get("dimensions", {})
            lines.append(
                f"- {idx}. {f['code']} {f['name']}：评级 {analyst.get('rating', 'Neutral')}，"
                f"Conviction {analyst.get('conviction', '-')}，Risk Gate {analyst.get('risk_gate', 'Watch')}；"
                f"催化 {dimensions.get('catalyst', '-')}，历史优势 {dimensions.get('history_edge', '-')}，"
                f"质量代理 {dimensions.get('quality_proxy', '-')}，动量 {dimensions.get('momentum', '-')}，"
                f"新闻 {dimensions.get('news', '-')}，风险质量 {dimensions.get('risk', '-')}；"
                f"原始因子：信号 {factors['signal']}，历史 {factors['history']}，"
                f"动量 {factors['momentum']}，新闻 {factors['news']}，风险扣分 {factors['risk_penalty']}。"
            )
            scenario = analyst.get("scenario")
            if scenario:
                lines.append(
                    f"  情景：{_cell(scenario.get('bull', ''))} / {_cell(scenario.get('base', ''))} / {_cell(scenario.get('bear', ''))}"
                )
        lines.append("")

    lines.append("## 使用口径")
    lines.append("")
    lines.append("- 优先看 Analyst Score、Conviction、Risk Gate 是否同时靠前。")
    lines.append("- Buy/Outperform 只代表候选优先级，仍需结合开盘流动性、公告和仓位管理。")
    lines.append("- 若开盘直接高开过多或接近涨停，报告候选应改为观察，不建议追价。")
    lines.append("- 出现减持、处罚、立案、业绩暴雷等负面新闻时，即使排名靠前也要降级处理。")
    lines.append("- 每次交易前重新检查公告、停牌、涨跌停、流动性和仓位。")
    lines.append("")
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], markdown: str, output_dir: Union[str, Path]) -> Tuple[Path, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{result['date']}-morning"
    md_path = out_dir / f"{prefix}.md"
    json_path = out_dir / f"{prefix}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def _cell(value: str) -> str:
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _change_cell(formatted: dict[str, Any]) -> str:
    meta = " · ".join(str(part) for part in [formatted.get("day_pct_source"), formatted.get("day_pct_asof")] if part)
    value = formatted.get("day_pct", "未知")
    return _cell(f"{value}（{meta}）" if meta else value)
