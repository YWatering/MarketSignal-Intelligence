# MarketSignal Intelligence

MarketSignal Intelligence 是一个面向中国股票市场和美国股票市场的 Agent Skill。它可以针对单只股票采集行情、财务、公告与相关新闻，完成清洗、去重、范围处理、中文或英文关键词舆情分类、指标计算、数据质量检查，并生成可追溯的 Excel 报告。

当前仓库已完成阶段二的多市场改进：支持中国 A 股、B 股、港股和美股。股价预测、多股票比较和定期任务仍在后续计划中，当前版本不会将这些能力表述为已实现。

## 当前能力

- 识别 A 股、B 股、港股和美股代码，并统一输出标准代码、数据源代码、市场和交易币种。
- 在线获取中国 A 股、B 股和港股日线行情，默认使用 AKShare，并对部分接口提供备用路由。
- 在线获取中国股票新闻，使用 AKShare 的股票新闻接口，并支持中文关键词舆情分类。
- 在线获取中国 A 股、B 股财务报表，统一提取收入、净利润、资产、负债、流动资产、流动负债、现金及经营现金流等指标。
- 在线获取港股年度财务报表和公司主体信息。
- 在线获取美股行情、Yahoo Finance RSS 新闻、SEC EDGAR 主体映射、公司事实和 10-K、10-Q、8-K 申报历史。
- 在线获取中国 A 股公告；B 股会尝试个股公告接口，若数据源没有可用结果则明确记录；港股公告在当前阶段记录为未配置专用适配器。
- 清洗重复、无效和超出请求范围的数据，并在工作簿中提供数据质量统计。
- 使用透明的中文或英文关键词规则对新闻进行正面、负面或中性分类，并保留命中的关键词依据。
- 计算行情、财务、新闻和公告的基础衍生指标，并记录计算方法。
- 使用本地缓存保存在线响应，记录缓存状态，避免重复请求。
- 生成带筛选、冻结表头、数值格式、新闻链接和来源记录的 Excel 工作簿。
- 保留数据源和处理状态；Excel 不写入文档创建、更新或生成日期元数据。

## 支持的市场代码

| 市场 | 标准输入示例 | 标准输出 | 交易币种 | 财务数据范围 |
| --- | --- | --- | --- | --- |
| 中国 A 股 | `600519`、`600519.SH` | `600519.SH` | CNY | A 股利润表、资产负债表、现金流量表 |
| 中国 B 股 | `900901`、`200002.SZ` | `900901.SH` 或 `200002.SZ` | 上海 B 股 USD；深圳 B 股 HKD | B 股利润表、资产负债表、现金流量表 |
| 港股 | `00700`、`00700.HK`、`HK00700` | `00700.HK` | HKD | 港股年度利润表、资产负债表、现金流量表 |
| 美股 | `AAPL`、`NASDAQ:AAPL` | `AAPL` | USD | SEC 公司事实中的配置指标 |

也可以显式传入 `--market cn_a`、`--market cn_b`、`--market hk` 或 `--market us`。市场不明确时，代码会依据代码格式推断；带有冲突交易所后缀的输入会直接报错。

## 环境要求

- Python 3.10 或更高版本。
- 中国市场在线模式需要可访问 AKShare 所使用的数据接口。
- 美股在线模式需要可访问 Twelve Data、Yahoo Finance RSS 和 SEC EDGAR。
- 美股行情需要 Twelve Data API Key。
- 美股 SEC 采集需要包含联系邮箱的 User-Agent。

## 安装

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 运行固定样例

固定样例使用仓库内的 AAPL 行情、新闻、主体、财务和公告夹具，部分数据包含重复或无效记录，用于验证清洗、映射、指标计算与数据质量统计。固定样例目前用于美股回归验证；中国市场使用在线模式。

```bash
.venv/bin/python scripts/run_sample.py
```

运行完成后会生成：

```text
outputs/aapl_market_signal.xlsx
```

## 中国市场在线采集

中国市场不需要 Twelve Data API Key 或 SEC User-Agent。以贵州茅台为例：

```bash
.venv/bin/python scripts/marketsignal.py \
  --symbol 600519 \
  --market cn_a \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --mode online \
  --output outputs/maotai_market_signal.xlsx
```

港股示例：

```bash
.venv/bin/python scripts/marketsignal.py \
  --symbol 00700.HK \
  --market hk \
  --mode online \
  --output outputs/tencent_market_signal.xlsx
```

中国市场运行时会按以下范围采集：

- 行情：A 股优先使用 AKShare A 股历史接口，必要时回退到腾讯行情接口；B 股使用 B 股日线接口；港股优先使用港股历史接口，必要时回退到港股日线接口。
- 新闻：使用 AKShare 股票新闻接口，保留标题、正文摘要、发布时间、来源和链接。
- 财务：A 股和 B 股使用利润表、资产负债表、现金流量表；港股使用年度利润表、资产负债表、现金流量表。
- 公告：A 股尝试使用个股公告接口；B 股使用同一接口并在无可用返回时记录为未支持；港股当前返回空公告集并在 `数据来源` 中标记为未配置专用适配器。

## 美股在线采集

设置 Twelve Data API Key 和 SEC User-Agent 后运行：

```bash
export MARKETSIGNAL_TWELVE_DATA_API_KEY="your-key"
export MARKETSIGNAL_SEC_USER_AGENT="MarketSignal Intelligence contact@example.com"
.venv/bin/python scripts/marketsignal.py \
  --symbol AAPL \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --mode online \
  --output outputs/aapl_market_signal.xlsx
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `--symbol` | 单个股票代码，如 `600519`、`900901`、`00700.HK` 或 `AAPL`。 |
| `--market` | 可选，`cn_a`、`cn_b`、`hk` 或 `us`；用于覆盖自动推断。 |
| `--start-date` | 可选，分析范围起点，格式为 `YYYY-MM-DD`。 |
| `--end-date` | 可选，分析范围终点，格式为 `YYYY-MM-DD`。 |
| `--mode` | `fixture` 使用固定样例，`online` 使用在线数据源。 |
| `--output` | 必填，输出 `.xlsx` 文件路径。 |
| `--twelve-data-api-key` | 美股可选，可替代环境变量传入 Twelve Data API Key。 |
| `--sec-user-agent` | 美股可选，可替代环境变量传入 SEC User-Agent，必须包含联系邮箱。 |
| `--cache-dir` | 可选，在线响应缓存目录。 |
| `--refresh-cache` | 可选，忽略已有缓存并重新请求数据源。 |
| `--announcement-limit` | 可选，最多保留的公告或 SEC 申报记录数。 |

在线模式在缺少必要凭据、网络请求失败、接口返回异常或没有有效数据时会返回错误，不会将失败伪装为成功结果。

## Excel 输出

生成的工作簿包含以下工作表：

| 工作表 | 内容 |
| --- | --- |
| `README` | 请求参数、标准代码、市场、币种、数据源、行数、限制说明和处理状态。 |
| `主体信息` | 股票代码、数据源代码、公司名称、市场、币种、交易所、CIK（美股可用）和来源。 |
| `行情数据` | 清洗后的日线开高低收、成交量、市场、币种和数据源。 |
| `财务数据` | 统一后的财务报告期间、申报或公告日期、指标、单位、市场、币种和来源。 |
| `新闻舆情` | 新闻发布时间、标题、来源、链接、摘要、中文或英文规则分类和关键词依据。 |
| `公告数据` | 中国 A 股/B 股公告或美股 SEC 申报日期、报告期、标题和链接。 |
| `指标分析` | 行情、财务、新闻和公告的衍生指标、单位、方法和证据。 |
| `数据来源` | 数据提供方、运行模式、缓存状态、来源地址、原始行数、清洗行数、输出行数、市场、币种和说明。 |
| `数据质量` | 原始记录数、清洗后记录数、输出记录数、重复记录数、无效记录数、范围外记录数和总体状态。 |

交易日、新闻发布时间、报告期、公告日期和请求范围属于业务数据，会保留在分析结果中；文档和文件名不添加创建、更新或生成日期标签。

## 项目结构

```text
.
├── SKILL.md                    # Agent Skill 入口与使用规则
├── agents/openai.yaml          # Skill 界面元数据
├── scripts/marketsignal.py     # 多市场采集、清洗、指标计算和 Excel 导出
├── scripts/run_sample.py       # 固定样例入口
├── fixtures/                   # 美股行情、新闻、主体、财务和申报测试数据
├── references/data_contract.md # 输入、输出、数据字段和错误约定
├── tests/                      # 自动化测试
├── outputs/                    # 示例 Excel 输出
├── 工作目标.md                  # 项目目标
└── 工作计划.md                  # 分阶段计划与完成记录
```

## 验证

运行自动化测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖美股阶段一回归、四类市场代码解析、B 股币种、中文行情与新闻归一化、中文财务指标提取、财务报告期间区分、范围过滤、SEC User-Agent、URL 脱敏、响应缓存和固定样例 Excel 生成。阶段二的实施方式与实际验证记录见 [工作计划.md](工作计划.md)。

## 使用边界

- AKShare、Twelve Data、Yahoo Finance RSS 和 SEC EDGAR 的可用性、覆盖范围、接口参数和请求限制可能变化。
- 中国 A 股、B 股和港股的财务字段由不同接口提供，当前统一提取配置指标，不保证覆盖全部会计科目或所有报告口径。
- 港股公告当前没有专用适配器，报告会明确记录该数据集未配置，不以猜测替代。
- B 股交易币种按交易所区分为上海 USD、深圳 HKD；财务报表若提供自身货币字段，则以报表货币字段为准。
- 新闻分类是小型可解释关键词规则，不等同于通用情感模型。
- 当前版本不包含价格预测、多股票比较或定期任务。
- 输出用于研究辅助，不构成投资建议，也不保证任何收益或价格变动。
