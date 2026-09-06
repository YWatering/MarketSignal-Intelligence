# Stage-Three Forecasting Rules

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
- Close deviation from five-day and twenty-day moving averages.
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

## Models

### Last-Close Baseline

`last_close_baseline` predicts zero return, so the predicted next close equals the current close. It is required as a realistic hurdle for the more complex model.

### Multi-Signal Ridge

`multisignal_ridge` standardizes features using training-fold means and standard deviations, then fits a linear return model with L2 regularization. The intercept is not penalized. Fitting is deterministic and uses no random split.

Predicted returns are bounded using the historical target-return distribution, with a lower absolute cap of 2% and an upper cap of 20%. This is a numerical stability control, not a market price-limit rule.

## Validation

Use expanding-window one-step-ahead walk-forward validation:

1. Keep samples in chronological order.
2. Reserve the requested number of final samples for validation, subject to retaining at least 40 earlier training samples.
3. For each validation target, fit feature scaling and the ridge model only on earlier samples.
4. Predict the next close for both models.
5. Append the actual and predicted values to `回测明细`.

Report:

- MAE in price units.
- RMSE in price units.
- MAPE as a percentage of actual close.
- Return MAE in percentage points.
- Three-way direction accuracy for rise, flat, or fall.

Select the lower-RMSE model, using MAE as the tie-breaker. Do not override the result to favor the more complex model.

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
