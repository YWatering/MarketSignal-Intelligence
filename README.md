# MarketSignal Intelligence

MarketSignal Intelligence 是一个面向中国股票市场和美国股票市场的 Agent Skill。它可以针对单只或多只明确指定的股票采集行情、财务、公告与相关新闻，完成清洗、去重、范围处理、中文或英文关键词舆情分类、指标计算、数据质量检查、预测、横向比较和可追溯 Excel 交付。

当前仓库已完成阶段四：在阶段三真实数据预测基础上，增加了多股票、用户定义行业或主题篮子、可重复任务清单、失败重试与隔离、结构化日志、行情多源交叉校验和统一组件版本管理。

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
- 使用同一次在线运行中阶段二采集并清洗后的真实行情、财务、新闻和公告构建预测特征。
- 使用上一收盘价基准模型和多信号岭回归模型进行比较，按走步回测 RMSE 自动选择模型。
- 输出 MAE、RMSE、MAPE、收益率 MAE、方向准确率、逐日预测与实际值对比、90% 经验预测区间和特征贡献。
- 对预测特征执行时间可用性约束，禁止使用目标日之后的价格、财务申报、新闻和公告。
- 使用版本化 YAML/JSON 清单运行多股票、投资组合、行业篮子或主题篮子任务，成分股必须由用户明确提供。
- 对批量任务逐股重试并隔离失败，已完成主体不会因其他主体失败而丢失。
- 输出 JSONL 结构化日志，并通过状态文件支持由外部调度器重复调用及间隔控制。
- 对中国 A 股和港股提供可选的第二行情源收盘价交叉校验，不一致时保留主数据并给出警告。
- 对 Skill、数据契约、批量契约、数据源适配器、预测引擎、模型和 Excel 模板进行集中版本管理。
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

## 阶段三真实数据预测

预测必须同时使用 `--mode online` 和 `--forecast`。固定样例、夹具数据或不足 120 条的行情记录不能生成正式预测。

以贵州茅台为例：

```bash
.venv/bin/python scripts/marketsignal.py \
  --symbol 600519 \
  --market cn_a \
  --start-date 2024-01-01 \
  --end-date YYYY-MM-DD \
  --mode online \
  --forecast \
  --forecast-horizon 5 \
  --output outputs/maotai_forecast.xlsx
```

预测流程如下：

1. 阶段二在线采集并清洗行情、财务、新闻和公告。
2. 以每个交易日为信息截止点构造技术面、基本面、新闻舆情和公告特征。
3. 使用扩展窗口逐日回测，训练集始终早于被预测交易日。
4. 比较上一收盘价基准模型与多信号岭回归模型的回测 RMSE 和 MAE。
5. 选择回测误差较低的模型作为主要结果，同时保留两个模型的预测和评估。
6. 根据回测绝对误差生成 90% 经验预测区间，并随预测步数扩大不确定性范围。

岭回归使用的特征包括短期收益、移动平均偏离、历史波动率、成交量变化、近七日新闻数量与情绪、近三十日公告数量、净利率、流动比率、经营现金流率和收入变化。财务数据只有在 `filed_date` 不晚于特征日时才允许进入模型。

## 阶段四多主体任务

批量任务使用版本化清单。`portfolio`、`industry` 和 `theme` 都要求明确列出股票，不自动猜测行业或主题成分。

仓库提供贵州茅台与五粮液的白酒行业示例：

```bash
.venv/bin/python scripts/market_batch.py \
  --manifest examples/china_liquor.yaml \
  --dry-run

.venv/bin/python scripts/market_batch.py \
  --manifest examples/china_liquor.yaml \
  --force
```

`--dry-run` 只验证清单和主体代码。正常运行会为每只股票生成详细工作簿，并生成：

```text
outputs/china_liquor_batch.xlsx
```

清单中的 `execution.attempts` 控制逐股重试，`continue_on_error` 控制某只股票失败后是否继续。配置 `schedule.interval_hours` 后，命令可由外部调度器重复调用；未到执行间隔时返回 `skipped`，`--force` 可以绕过间隔检查。运行日志和调度状态默认保存在 `.cache`，不会提交凭据。

单股命令也可以请求行情交叉校验：

```bash
.venv/bin/python scripts/marketsignal.py \
  --symbol 600519 \
  --market cn_a \
  --mode online \
  --cross-validate-prices \
  --cross-validation-tolerance-pct 1.0 \
  --output outputs/maotai_market_signal.xlsx
```

交叉校验按交易日对齐两个来源的收盘价，报告重叠行数、平均和最大差异、超过容差的行数。校验结果不会覆盖或平均主行情数据。批量横向比较还会汇总区间收益率、收益波动率、净利率、流动比率、经营现金流率和新闻情绪数量。

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
| `--forecast` | 可选，运行阶段三预测；只允许与 `online` 模式一起使用。 |
| `--forecast-horizon` | 可选，未来预测步数，范围为 1 至 20，默认 5。 |
| `--forecast-minimum-history` | 可选，预测所需最少清洗后行情行数，最低可设为 80，默认 120。 |
| `--forecast-validation-points` | 可选，扩展窗口回测的验证行数，默认 40。 |
| `--forecast-ridge-alpha` | 可选，岭回归正则化强度，必须大于 0，默认 1.0。 |
| `--cross-validate-prices` | 可选，对支持的市场请求第二行情源收盘价校验。 |
| `--cross-validation-tolerance-pct` | 可选，交叉校验允许的收盘价差异百分比，默认 1.0。 |

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
| `预测结果` | 两个模型的未来点预测、收益率、预测区间、数据截止日、预测步数和是否入选。仅预测运行生成。 |
| `模型评估` | MAE、RMSE、MAPE、收益率 MAE、方向准确率、训练与验证范围、防泄漏规则和模型选择。仅预测运行生成。 |
| `回测明细` | 扩展窗口逐日预测、实际收盘价、误差、方向判断和当次训练样本数。仅预测运行生成。 |
| `特征贡献` | 岭回归标准化系数、影响方向、当前特征值和特征解释。仅预测运行生成。 |
| `交叉校验` | 主副行情源、重叠记录数、价格差异、允许容差和校验状态。仅请求交叉校验时生成。 |
| `数据来源` | 数据提供方、运行模式、缓存状态、来源地址、原始行数、清洗行数、输出行数、市场、币种和说明。 |
| `数据质量` | 原始记录数、清洗后记录数、输出记录数、重复记录数、无效记录数、范围外记录数和总体状态。 |
| `版本信息` | Skill、数据契约、批量契约、数据源、预测引擎、模型和 Excel 模板版本。 |

批量汇总工作簿包含 `README`、`主体任务`、`横向比较` 和 `版本信息`。`横向比较` 使用统一字段展示行情表现、财务比率、新闻情绪、数据覆盖和预测结果；每只股票的完整明细仍保留在独立工作簿中。

交易日、新闻发布时间、报告期、公告日期和请求范围属于业务数据，会保留在分析结果中；文档和文件名不添加创建、更新或生成日期标签。

## 项目结构

```text
.
├── SKILL.md                    # Agent Skill 入口与使用规则
├── agents/openai.yaml          # Skill 界面元数据
├── scripts/marketsignal.py     # 多市场采集、清洗、指标计算和 Excel 导出
├── scripts/forecasting.py      # 特征构造、走步回测、模型训练和未来预测
├── scripts/market_batch.py     # 多主体清单、重试、隔离、调度状态和汇总导出
├── scripts/operations.py       # 结构化日志、重试、间隔判断和行情交叉校验
├── scripts/versioning.py       # 组件版本注册表
├── scripts/run_sample.py       # 固定样例入口
├── scripts/run_acceptance.py   # 确定性端到端验收入口
├── examples/                   # 多股票、行业或主题任务清单示例
├── fixtures/                   # 美股行情、新闻、主体、财务和申报测试数据
├── references/data_contract.md # 输入、输出、数据字段和错误约定
├── references/forecasting.md   # 阶段三模型、特征、防泄漏和评估规则
├── references/batch_contract.md # 阶段四批量任务清单契约
├── references/operations.md    # 交叉校验、重试、日志、调度和版本规则
├── tests/                      # 自动化测试
├── outputs/                    # 示例 Excel 输出
├── 工作目标.md                  # 项目目标
└── 工作计划.md                  # 分阶段计划与完成记录
```

## 验证

运行自动化测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_acceptance.py
```

31 项测试覆盖早期阶段回归、四类市场、数据治理、真实数据预测、时间可用性、多主体清单、重试恢复、失败隔离、调度间隔、行情交叉校验、版本注册和 Excel 契约。`run_acceptance.py` 使用固定样例执行完整批量入口、单股报告和汇总报告验收。阶段实施方式与实际验证记录见 [工作计划.md](工作计划.md)。

## 使用边界

- AKShare、Twelve Data、Yahoo Finance RSS 和 SEC EDGAR 的可用性、覆盖范围、接口参数和请求限制可能变化。
- 中国 A 股、B 股和港股的财务字段由不同接口提供，当前统一提取配置指标，不保证覆盖全部会计科目或所有报告口径。
- 港股公告当前没有专用适配器，报告会明确记录该数据集未配置，不以猜测替代。
- B 股交易币种按交易所区分为上海 USD、深圳 HKD；财务报表若提供自身货币字段，则以报表货币字段为准。
- 新闻分类是小型可解释关键词规则，不等同于通用情感模型。
- 预测使用历史关系估计未来，不代表因果关系；突发事件、停牌、涨跌停、制度变化和数据源变化都可能使模型失效。
- 未来交易日目前按周一至周五估算，不包含交易所节假日日历；预测区间是基于回测误差的经验区间，不是收益保证。
- 多步预测采用递归价格路径，未来新闻、公告和财务输入固定在数据截止点，因此预测步数越远，不确定性越高。
- 行业和主题任务不会自动发现成分股，需要用户提供明确列表；当前横向比较不执行自动选股或组合优化。
- 仓库提供可重复调用和间隔判断，不运行常驻调度服务；实际触发频率由外部调度器负责。
- 行情交叉校验当前支持中国 A 股和港股；中国 B 股与美股会明确返回未配置第二适配器。
- 输出用于研究辅助，不构成投资建议，也不保证任何收益或价格变动。
