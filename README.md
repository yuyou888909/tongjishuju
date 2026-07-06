# 九点猫研 CatDesk 9

每天 9 点，猫一眼市场机会。

每天早上 9 点生成一份“昨夜美股 -> 今日 A 股候选”的早报。它把美股主题涨跌、A 股历史次日表现、个股动量、新闻催化和风险提示合成一个评分，输出每个板块 5 只候选股。

> 输出是研究候选和风险提示，不是无条件买入指令。真实交易还需要结合账户风险承受能力、仓位、止损、流动性和当天开盘价格。

## 数据源

默认优先使用轻量直连接口，并保留 AkShare 作为显式备用：

- 美股日线：新浪单 ticker 静态日线，Yahoo chart 备用
- A 股实时行情：东方财富轻量 JSON
- A 股历史日线：东方财富轻量 JSON
- 个股新闻：东方财富新闻搜索 JSONP
- AkShare：默认不启用慢回退；需要诊断时加 `--allow-akshare-fallback`

外部接口会随上游网站变化而波动，所以工具内置了缓存和失败降级。第一次跑真实数据前先安装依赖。

## 快速开始

```bash
git clone https://github.com/yespsam/a-share-us-catalyst.git
cd a-share-us-catalyst
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m ashare_us_catalyst.cli --top 5
```

报告会写入：

```text
reports/YYYY-MM-DD-morning.md
reports/YYYY-MM-DD-morning.json
```

如果只是想检查格式和算法，不联网也可以跑样例数据：

```bash
PYTHONPATH=src python -m ashare_us_catalyst.cli --sample-data --top 5
```

## 每天 9 点自动运行

macOS 本地定时任务：

```bash
./scripts/install_launchd.sh
```

它会安装一个 `launchd` job，每个交易日早上 9 点执行 `scripts/run_report.sh`，把报告写入本机 `reports/`。日志在：

```text
logs/launchd.out.log
logs/launchd.err.log
```

## 公网 Netlify 每天 9 点更新

公网网页是静态部署，必须重新构建 `dist/data/*.json` 并发布到 Netlify 才会更新。安装公网更新任务：

```bash
./scripts/install_public_launchd.sh
```

它会在每周一至周五 09:00 执行 `scripts/run_public_site_update.sh`，重新生成网页数据并运行：

```bash
npx netlify deploy --prod --dir=dist --functions=netlify/functions
```

这个任务依赖本机保持开机、联网，并且 Netlify CLI 已登录和链接到站点。日志在：

```text
logs/public-launchd.out.log
logs/public-launchd.err.log
```

## 网页版

```bash
./scripts/run_web.sh
```

打开：

```text
http://127.0.0.1:8765
```

网页包含：

- A 股早报：美股前夜主题 -> A 股板块和个股候选
- 美股优选：按质量、成长、估值、护城河、趋势和风险筛选美股候选
- 日韩市场：覆盖日本、韩国核心资产，保留汇率、政策、财报和跨境交易风险提示
- 高倍潜力：挖掘早期高弹性候选，结合产业空间、市值甜点、趋势初段、资金确认、新闻催化和风险闸门
- 回测证据：展示过去半年候选算法的历史表现
- 每日日报：网页内阅读完整早报文字版
- 报告历史：读取本地 `reports-web/`

美股“买入候选”是研究分层，不是保证收益或无条件买入指令。

## 公开仓库说明

本仓库只包含系统源码、网页、配置、测试和可公开的视觉资产。运行缓存、每日报告、Netlify 本地状态、渲染视频、个人声音样本和音频混音文件不会提交到 GitHub。

本工具仅以娱乐和公开资料整理为主，不构成投资建议，不代表确定收益。

## Telegram 推送

复制环境变量模板：

```bash
cp .env.example .env
```

填入：

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

然后：

```bash
PYTHONPATH=src python -m ashare_us_catalyst.cli --send-telegram
```

## 调整股票池

编辑 `config/themes.json`：

- `us_tickers`：昨夜美股信号股票，支持 `weight` 权重
- `candidates`：A 股候选池，工具会从里面按历史有效性、实时行情和新闻筛选 Top 5
- `logic`：板块映射逻辑，会进入报告

编辑 `config/multibagger.json`：

- `us_universe`：早期高弹性美股候选池，包含阶段、赛道、市值档、成长、估值、护城河、催化和风险标签
- A 股高倍潜力候选默认复用 `config/themes.json` 中的 A 股股票池
- 每次公网更新会生成 `dist/data/multibagger.json`

## 评分口径

单只 A 股总分由以下部分构成：

- 美股主题信号：昨夜同主题美股加权涨跌幅
- 历史有效性：过去一段时间里，美股主题上涨后 A 股次日上涨概率、平均收益、相关性和 beta
- 个股动量：A 股最近 20 日收益、60 日波动和当天涨跌幅
- 新闻催化：近期新闻标题中的订单、业绩、回购、合作、政策等正向词，以及减持、处罚、亏损等风险词
- 风险扣分：ST、近期涨幅过热、当天大幅高开/涨停附近、负面新闻

默认按板块输出 Top 5。每只股票包含：代码、名称、产业、市值、评分、历史胜率、利好逻辑和近期重大新闻。

早期高倍潜力模块额外关注：

- 市值甜点：偏好仍有重估空间但流动性可接受的中小市值区间
- 产业期权：AI、半导体、机器人、商业航天、量子、创新药、储能等高空间赛道
- 趋势初段：避免已经极度透支的后期标的，优先看启动确认和主升初段
- 资金确认：20 日趋势、60/120 日收益、换手率、量比和回撤位置
- 风险闸门：过热、ST/退市风险、负面新闻、估值过高、融资和商业化不确定性

## 常用命令

```bash
# 真实数据
PYTHONPATH=src python -m ashare_us_catalyst.cli --top 5

# 样例数据
PYTHONPATH=src python -m ashare_us_catalyst.cli --sample-data --top 5

# 只看前 4 个信号最强板块
PYTHONPATH=src python -m ashare_us_catalyst.cli --max-themes 4

# 不抓新闻，加快速度
PYTHONPATH=src python -m ashare_us_catalyst.cli --skip-news

# 允许 AkShare 作为慢回退源
PYTHONPATH=src python -m ashare_us_catalyst.cli --allow-akshare-fallback

# 回看 500 天
PYTHONPATH=src python -m ashare_us_catalyst.cli --lookback-days 500
```
