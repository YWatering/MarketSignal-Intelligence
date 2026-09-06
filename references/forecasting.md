# Stage-Five Forecasting Rules

Read this reference when a user requests price prediction, model evaluation, backtesting, or forecast interpretation.

## Data Requirement

Formal forecasts must be produced from the cleaned data returned by the same online stage-two run:

- Daily OHLCV prices provide the prediction target and technical features.
- Financial statements provide point-in-time fundamental ratios.
- News provides recent article count and rule-based tone balance.
- Notices or filings provide recent event-count features.

Fixture mode and fixture records are prohibited for formal forecasts. Cached online responses remain valid because they originate from the online source adapters and retain their source identity.

The default minimum is 120 clean price rows. The configured minimum cannot be lower than 80 because feature construction requires trailing windows plus separate training and validation observations.

## Prediction Target

The target is the next observed daily close return:

```text
target_return = next_close / current_close - 1
predicted_close = current_close * (1 + predicted_return)
```

Each supervised sample uses features available at the current trading observation to predict the next trading observation.

## Features

Technical features:

- One-day and five-day close return.
- Close deviation from five-day and twenty-day moving averages and the ten-day exponential average.
- Twenty-day close-return volatility.
- Current volume relative to twenty-day average volume.

News and notice features:

- Positive minus negative news count over the trailing seven calendar days.
- News count over the trailing seven calendar days.
- Notice or filing count over the trailing thirty calendar days.

Fundamental features:

- Net profit margin.
- Current ratio.
- Operating cash flow margin.
- Comparable-duration revenue growth.
- Fundamental feature coverage.

Financial features use only rows whose `filed_date` is on or before the feature date. Missing ratios are set to zero and accompanied by `fundamental_coverage`, allowing the model to distinguish no coverage from a complete set of ratios.

## Candidate Models

### Last-Close Baseline

`last_close_baseline` predicts zero return, so the predicted next close equals the current close. It is required as a realistic hurdle for the more complex model.

### Moving-Average Baseline

`moving_average_baseline` predicts a return that moves the next close toward the trailing five-day moving average. It is a transparent mean-reversion hurdle, not a guarantee that prices revert.

### Historical-Mean Baseline

`historical_mean_baseline` predicts the mean next-period return observed in the earlier training samples.

### Exponential-Smoothing Baseline

`exponential_smoothing_baseline` predicts a return that moves the next close toward a ten-day exponential moving average.

### Multi-Signal Ridge

`multisignal_ridge` standardizes features using training-fold means and standard deviations, then fits a linear return model with L2 regularization. The intercept is not penalized. Fitting is deterministic and uses no random split.

Predicted returns are bounded using the historical target-return distribution, with a lower absolute cap of 2% and an upper cap of 20%. This is a numerical stability control, not a market price-limit rule.

## Validation And Selection

Use expanding-window one-step-ahead walk-forward validation:

1. Keep samples in chronological order.
2. Reserve the requested number of final samples for validation, subject to retaining at least 40 earlier training samples.
3. For each validation target, fit each model only on earlier samples; ridge scaling is also fit only on that training fold.
4. Predict the next close for every candidate model.
5. Append the actual and predicted values to `回测明细`.

Report:

- MAE in price units.
- RMSE in price units.
- MAPE as a percentage of actual close.
- Return MAE in percentage points.
- Three-way direction accuracy for rise, flat, or fall.
- Macro-averaged three-way direction F1.
- Mean price and return bias.
- Empirical interval coverage and width.

The selector combines normalized price performance and direction accuracy. The default weights are 65% price performance and 35% direction performance. Each candidate must first pass robustness and relative-performance checks; the last-close baseline is always eligible as the minimum performance hurdle.

For `multisignal_ridge`, selection also requires either the configured minimum price RMSE improvement or the configured minimum direction improvement against the last-close baseline. The default gates are 2% price improvement or 5 percentage points of direction improvement, while allowing no more than 1% price deterioration or 5 percentage points of direction deterioration. Every row stores an explicit selection reason.

Do not override the result to favor the more complex model.

## Robustness Checks

The default validation windows are 20, 40, and 80 samples, shortened only when the available history cannot support the requested size. Each candidate is evaluated on the complete window and on low- and high-volatility slices based on the validation-window median of `volatility_20`. The `模型稳健性` sheet records metrics and a `robustness_status` for every measured window and regime.

Model parameters, feature definitions, score weights, and improvement gates are fixed before the validation result is inspected. These checks reduce single-window selection bias but do not replace independent live monitoring.

## Transaction-Cost Sensitivity

The backtest converts each predicted direction into a simple research signal in `{-1, 0, 1}`. It reports `no_cost`, `slippage_only`, and `base_cost` scenarios. Each scenario includes cumulative gross and net return, maximum drawdown, active-signal win rate, turnover, trade count, and total modeled cost. This is a sensitivity analysis, not an execution simulator or return guarantee.

## Prediction Interval

For each model, calculate the configured empirical absolute-error quantile from one-step walk-forward residuals. The current implementation uses the 90th percentile. Scale interval width by the square root of forecast step:

```text
interval_width(step) = validation_absolute_error_quantile * sqrt(step)
```

This interval summarizes observed validation error under the implemented assumptions. It is not a confidence guarantee and may not cover regime changes or discontinuous events.

## Multi-Step Forecasting

- Step one uses the last real stage-two price and all external information available at that cutoff.
- Later steps append predicted closes to the technical price path.
- Future volume uses the recent average only as a recursive technical input.
- Financial, news, and notice inputs remain fixed at the last real data cutoff.
- Estimated trading dates skip weekends but do not account for exchange holidays or suspensions.

## Interpretation

- Treat coefficients as standardized model associations, not causal effects.
- Prefer validation metrics and interval width over the point estimate alone.
- State when the baseline wins; this indicates that the richer feature set did not improve the selected validation period.
- Do not convert a predicted return into a certain trading recommendation.
- Explain that sudden news, policy changes, corporate actions, market closures, and data-source revisions can invalidate historical relationships.
