# Start here — synthetic-only ML demonstration

Completed 25 October 2026, using the existing project monthly trainer.

## Measured results

| Target | Selected method | Test WAPE | Last-value baseline WAPE | Test rows |
|---|---|---:|---:|---:|
| demand | ridge_absolute | 7.340% | 14.309% | 24 |
| SALE | random_forest | 0.195% | 1.103% | 24 |
| PURCHASE | random_forest | 0.242% | 0.446% | 24 |

24 simulated months, six products, 4,380 sales lines and 255 purchase lines. Ten sample purchase PDFs and ten sample sales PDFs illustrate the source-style invoice headers; the complete CSVs were used for training.

The final four target months (June–September 2026) were held out. Earlier expanding folds selected the model. Evaluation is rolling one-step-ahead, using observed earlier test months, not four-month-ahead forecasting. Price features do not use the unknown future invoice quantity.

## Open these files

1. results/evaluation.json — complete metrics and limitations
2. results/synthetic-demand-holdout.pdf — actual versus predicted chart
3. results/next-month-product-plan.csv — simulated quantity, prices, revenue and stock advice
4. results/test-predictions.csv — all held-out predictions
5. results/verification.json and reproducibility.json — model reproduction and isolation checks
6. purchase and sales — separate complete datasets and twenty sample bills

The real-data demand WAPE remains 89.745%, and its forecasting remains experimental. Synthetic results cannot establish real-business accuracy. This run made no AWS calls and did not change the website or live inventory. The revised report includes both evaluations, without changing the first two pages.
