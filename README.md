# MarketSignal Intelligence

MarketSignal Intelligence 是一个面向证券市场研究的 Agent Skill。它用于收集单只股票的行情数据与相关新闻，完成基础清洗、去重、范围过滤和数据质量检查，并生成可追溯的 Excel 报告。

当前仓库已完成阶段一：可运行的最小版本。财务报表、公告、情感评分、股价预测、多股票比较和定期任务仍在后续计划中，当前版本不会将这些能力表述为已实现。

## 当前能力

- 输入单个股票代码、可选的分析范围和 Excel 输出路径。
- 在线获取日线行情与相关新闻：行情来自 Twelve Data，新闻来自 Yahoo Finance RSS。
- 提供固定样例模式，不依赖网络或 API Key，适合演示、测试和重复验证。
- 清洗重复、无效和超出请求范围的数据，并在工作簿中提供数据质量统计。
- 生成带筛选、冻结表头、数值格式和新闻链接的 Excel 工作簿。
- 保留数据源和处理状态；Excel 不写入核心时间元数据。

## 环境要求

- Python 3.10 或更高版本。
- 网络采集需要可访问 Twelve Data 和 Yahoo Finance RSS。
- 在线行情采集需要 Twelve Data API Key。

## 安装

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 运行固定样例

固定样例使用仓库内的 AAPL 行情与新闻夹具，包含重复和无效记录，用于验证清洗与数据质量统计。

```bash
.venv/bin/python scripts/run_sample.py
```

运行完成后会生成：

```text
outputs/aapl_market_signal.xlsx
```

## 在线采集

设置 Twelve Data API Key 后，运行单股票分析：

```bash
export MARKETSIGNAL_TWELVE_DATA_API_KEY="your-key"
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
| `--symbol` | 单个股票代码。 |
| `--start-date` | 可选，分析范围起点，格式为 `YYYY-MM-DD`。 |
| `--end-date` | 可选，分析范围终点，格式为 `YYYY-MM-DD`。 |
| `--mode` | `fixture` 使用固定样例，`online` 使用在线数据源。 |
| `--output` | 必填，输出 `.xlsx` 文件路径。 |
| `--twelve-data-api-key` | 可选，可替代环境变量传入 Twelve Data API Key。 |

在线模式在缺少 API Key、网络请求失败、接口返回异常或没有有效数据时会返回错误，不会将失败伪装为成功结果。

## Excel 输出

生成的工作簿包含以下工作表：

| 工作表 | 内容 |
| --- | --- |
| `README` | 请求参数、数据源、行数、限制说明和处理状态。 |
| `行情数据` | 清洗后的日线开高低收、成交量和数据源。 |
| `新闻舆情` | 清洗后的新闻发布时间、标题、来源、链接、摘要和未分类情感字段。 |
| `数据质量` | 原始记录数、清洗后记录数、重复记录数、无效记录数、范围外记录数和总体状态。 |

交易日、新闻发布时间和请求范围属于业务数据，会保留在分析结果中；文档和文件名不添加创建、更新或生成日期标签。

## 项目结构

```text
.
├── SKILL.md                    # Agent Skill 入口与使用规则
├── agents/openai.yaml          # Skill 界面元数据
├── scripts/marketsignal.py     # 采集、清洗、质量检查和 Excel 导出
├── scripts/run_sample.py       # 固定样例入口
├── fixtures/                   # 可重复使用的测试与演示数据
├── references/data_contract.md # 输入、输出和数据字段约定
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

测试覆盖行情清洗、新闻清洗、范围过滤、无效输入和固定样例 Excel 生成。更详细的阶段一实现方式和验证结果见 [工作计划.md](工作计划.md)。

## 使用边界

- 数据源可能受到 API 配额、服务限制、网络状态和覆盖范围影响。
- 当前版本不包含财务报表、公告数据、自动情感评分或价格预测。
- 输出用于研究辅助，不构成投资建议，也不保证任何收益或价格变动。
