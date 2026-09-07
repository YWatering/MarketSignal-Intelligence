# Stage-Six Machine-Learning Forecasting Rules

Read this reference for multi-stock machine-learning forecasts of benchmark-relative returns. Stage six is independent from the stage-five single-stock price forecast and does not silently replace it.

## Scope

The first implementation supports explicitly supplied China A-share panels. Each task must specify:

- At least two A-share symbols.
- One fixed benchmark index code and benchmark type.
- A business-data range.
- One or more horizons from 1 to 20 trading steps.
- A universe membership policy.
- Nested validation sizes and cost assumptions.

Run the workflow with `scripts/market_ml.py` and a version `1.0` manifest. Automatic constituent discovery and automatic benchmark substitution are prohibited.

## Target

Stock returns use forward-adjusted (`qfq`) prices. The target is:

```text
stock_return(h) = adjusted_close(t+h) / adjusted_close(t) - 1
benchmark_return(h) = benchmark_close(t+h) / benchmark_close(t) - 1
excess_return(h) = stock_return(h) - benchmark_return(h)
outperformed(h) = 1 when excess_return(h) > 0, otherwise 0
```

Every labeled row is keyed by `symbol + feature_date + target_date + horizon`. Latest prediction rows use the last trading date shared by all supplied stocks and the benchmark so cross-sectional ranks refer to one information cutoff.

## Features

The panel reuses stage-five point-in-time technical, financial, news, and notice features and adds:

- Twenty-day and sixty-day stock momentum.
- One-day, five-day, and twenty-day benchmark momentum.
- Five-day and twenty-day relative momentum.
- Sixty-day rolling beta and market correlation.
- Relative volatility, twenty-day amplitude, and sixty-day drawdown.
- Explicit industry and size-bucket indicators supplied by the manifest.
- Same-date cross-sectional ranks for momentum, volatility, net profit margin, and recent news tone.

Financial rows are available only after `filed_date`. News and notices are available only after their publication or filing time. Missing-value fitting, scaling, parameter selection, and feature importance use training data only within each split.

## Candidate Models

All candidates receive the same rows, labels, features, and time boundaries:

| Model | Role |
| --- | --- |
| `zero_excess` | Predicts that the stock matches the benchmark. |
| `excess_historical_mean` | Predicts the earlier training-window mean excess return. |
| `panel_ridge` | Stable standardized linear model with L2 regularization. |
| `elastic_net` | Stable standardized linear model with L1 and L2 regularization. |
| `random_forest` | Nonlinear bagged-tree model with limited depth and leaf size. |
| `hist_gradient_boosting` | Scikit-learn histogram gradient boosting with a small fixed search grid. |

The integrated histogram gradient boosting implementation was selected instead of an external XGBoost or LightGBM runtime so the Skill has no separate OpenMP system dependency. It still serves the planned purpose of testing a constrained gradient-boosted tree model.

## Nested Time Validation

Validation has three isolated levels:

1. Inner chronological validation selects parameters from a predefined finite grid.
2. Purged outer rolling windows compare models and lock one candidate before the final test is viewed.
3. The final holdout accepts or rejects the locked candidate; it is not used to change features, grids, parameters, or the locked outer candidate.

For every split, training labels must end before the first validation feature date. This removes overlap at the boundary for multi-step targets. Random K-Fold is not implemented.

## Metrics And Admission

Regression metrics include MAE, RMSE, out-of-sample R-squared, IC, and Rank IC. Direction and probability metrics include accuracy, F1, AUC, Brier Score, and calibration error.

Machine-learning models begin as `research_only`. A model can be promoted only when it:

- Beats the best simple model in a majority of outer windows.
- Has positive Rank IC in a majority of outer windows.
- Remains better than the simple hurdle on the untouched final holdout.
- Retains positive high-minus-low spread performance after configured cost and slippage.
- Has no leakage or data-governance warning.

If no machine-learning model passes every gate, the locked simple model remains `selected`. Results must never be overridden to make a more complex model appear successful.

## Economic Evaluation

For every target date, stocks are sorted by predicted excess return. The research spread is long the highest group and short the lowest group. The workbook reports no-cost, slippage-only, and base-cost scenarios with cumulative gross and net return, average spread, win rate, turnover, and maximum drawdown.

This is a model diagnostic. It does not model order books, borrow constraints, price limits, taxes, capacity, or executable fills.

## Explanations

Global importance uses coefficients, built-in tree importance, or training-only permutation importance depending on the estimator. Local explanations replace one feature at a time with its training median and measure the prediction difference. These values explain model associations, not causal effects.

## Governance Limit

`historical_constituents` passes the historical-membership check only when the user supplies auditable historical membership records. `user_supplied_fixed_universe` remains usable for research but receives a survivorship-bias warning, which prevents machine-learning promotion to `selected`.

## Workbook

The stage-six workbook includes `README`, `面板样本`, `机器学习预测`, `模型排行榜`, `滚动验证`, `特征重要性`, `预测解释`, `分组检验`, `成本敏感性`, `数据泄漏检查`, `最终测试明细`, `参数搜索`, `数据来源`, and `模型版本`.

Business dates remain in the data. OOXML document creation, update, and generation time metadata is removed.

## Single-Asset Mode

Stage seven adds `task_type: single_asset` without changing the panel contract. The task must contain exactly one explicitly supplied A-share stock and one explicitly supplied benchmark index. Stock and benchmark prices are aligned on common trading dates before labels and features are built.

Single-asset targets use the same definition:

```text
stock_return(h) = adjusted_close(t+h) / adjusted_close(t) - 1
benchmark_return(h) = benchmark_close(t+h) / benchmark_close(t) - 1
excess_return(h) = stock_return(h) - benchmark_return(h)
outperformed(h) = 1 when excess_return(h) > 0, otherwise 0
```

The single-asset mode keeps the following controls:

- Forward-adjusted stock prices and an explicit benchmark identity.
- Point-in-time financial, news, notice, stock, and benchmark-context features.
- Inner chronological parameter selection, purged outer windows, and a locked final holdout.
- The same six candidates, model states, rejection reasons, interval construction, and version registry.

The single-asset mode deliberately omits cross-sectional ranks, cross-sectional prediction ranks, high-minus-low groups, and grouped-spread tests. Time-series IC or Rank IC may be reported only over the available chronological evaluation rows and must identify its evaluation scope.

For economic diagnostics, the predicted excess-return sign becomes a relative signal: positive predictions represent long-stock-versus-benchmark exposure, negative predictions represent the opposite relative exposure, and zero predictions are neutral. The workbook reports gross and net cumulative relative return, win rate, turnover, and maximum drawdown under no-cost, slippage-only, and base-cost scenarios. These outputs are research diagnostics and do not model executable fills, borrowing, taxes, capacity, or market impact.

Historical membership is not applicable to one explicitly requested stock. The leakage report records this as a passed not-applicable check rather than claiming that the task reconstructs historical index membership.
