# Monthly bill profit forecasting

The active forecast is now one model: bill gross profit -> calendar-month total -> monthly Ridge Regression forecast. The former demand, selling-price and purchase-cost forecast screens and packaged model weights are retired. Historical training evidence remains archived; it does not describe this new model.

Bill gross profit is net sales minus the cost attributable to that bill. Buying inventory is not immediately the cost of every sale that month. The application uses an explicit recorded line cost or an exact product/unit/currency match to an earlier purchase rate. Dated purchase rates are an estimate of cost, not a FIFO inventory valuation. Service costs and missing matches remain unknown.

Real and synthetic bills are separate. The existing real source sales predate the purchase dataset, so their bill profits cannot be automatically established. Open Real invoices > Profit for each bill > Edit profit and enter a verified amount and note. Alternatively download the bill-profit CSV, fill gross_profit and note, check the verification checkbox, and import it. Enter losses as negative values. Confirmations are stored separately, with source fingerprints and an audit history; they do not approve an invoice, change stock or record a payment.

Every included bill in a month needs a profit before that month enters training. The real source bills are selected examples; the forecast describes those included recorded bills, not independently established whole-business profit. Missing months and missing costs are not filled with zero. At least two usable months are required to fit; with fewer than six, the result is exploratory.

The synthetic workspace computes profits for 730 generated daily sales bills using matching dated generated purchase rates. These give 24 monthly totals. One Ridge regression uses time trend; with at least 18 months it also uses annual calendar features and month length. Final chronological months are excluded from the evaluation fit. The development backtest was inspected during feature development and is not a fresh untouched experiment. Do not transfer the old demand/price accuracy or SageMaker training claim to this new monthly model.

The December 2026 forecast and current development errors are saved in the replacement's result folder. Numeric outputs are model estimates, not guaranteed realized net profit. Gross profit excludes operating expenses and tax. Training and predictions use portable standard-library Python in the existing API Lambda, without another paid SageMaker job or persistent endpoint.

API: GET /api/v1/forecasts/profit?source=real|synthetic&horizon=1|3|6&start_month=YYYY-MM. Real confirmations: POST /api/v1/forecasts/profit/confirmations. The old model endpoints are no longer registered.

## Measuring inference time

On Forecasts, open Model speed and click Measure inference time for the selected fitted model. The server measures 1,000 warm one-month predictions separately from forecast preparation; the browser measures the full timing-request round trip. Download the current timing result as JSON. Real timing remains disabled until enough verified bill profits support fitting. Full method and live verification: `demo-results/2026-11-04/inference-timing/README.md`.
