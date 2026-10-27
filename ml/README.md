# Sales forecasting and SageMaker lifecycle

## Trained demand model (2026-10-22)

The hosted forecast API now compares ridge regression using lag-1, lag-7,
seven-day mean, trend and weekly calendar features against recent mean,
weekly seasonal and linear-trend models. Model selection uses a chronological
validation interval. A separate final test interval reports MAE, RMSE and WAPE;
only after evaluation is the selected model refit on the full history. Histories
under 56 days are flagged as limited; very short histories use a baseline.
Training and inference run in the existing API Lambda without a SageMaker
endpoint. Saved JSON artifacts contain coefficients, recent context and source
hashes. The Forecasts page provides a model download and seven-day predictions.

`Train presentation demo` uses explicitly synthetic data for three products and
never creates invoices or inventory movements. Its metrics are demonstration
results, not evidence of business accuracy. To reproduce the standalone run:

```powershell
.\backend\.venv\Scripts\python.exe ml\src\train_demand.py --demo
```

For real, complete daily sales, replace `--demo` with `--input <verified.csv>`.
Artifacts are saved to `ml/artifacts/demand/model.json` and `forecasts.csv`.
The older moving-mean trainer and optional SageMaker workflow below remain
separate baseline tools; they are not the new demand model.

## Source data contract

The trainer requires `data/processed/sales_daily.csv` with columns
`product_id,date,quantity_sold`, exactly one row per product/day, nonnegative
quantities, and consecutive calendar days. Use only approved invoices
classified as `SALE`. Purchases are excluded. Do not infer zero-sales days
unless you have verified complete transaction coverage for that product and
period.

Approved invoice line descriptions need a human-reviewed exact mapping to a
stable product/SKU. Start with `data/manifests/sales_product_mapping_template.csv`.
Then, from `backend/`, export approved sales invoices:

```powershell
\.venv\Scripts\python.exe ..\ml\src\export_approved_sales.py `
  --mapping-csv ..\data\manifests\sales_product_mapping.csv `
  --output ..\data\processed\sales_daily.csv
```

The exporter emits only observed invoice dates by default; the trainer will
reject gaps instead of inventing zeros. Only if you have independently
confirmed complete source coverage, pass both `--coverage-start YYYY-MM-DD` and
`--coverage-end YYYY-MM-DD` to let the exporter add zero-sales calendar days.
The unverified toner balance and purchase invoices do not count as sales data.

## Local evaluation and fit

Install `requirements.txt`, then from `ml/` run:

```powershell
python -m pip install -r requirements.txt
python src/train_baseline.py `
  --input ../data/processed/sales_daily.csv `
  --output artifacts/baseline_model.json `
  --forecasts-output artifacts/forecasts.csv `
  --window-days 7 --horizon-days 7
```

The trainer evaluates on the final chronological holdout (up to the forecast
horizon) per product, reports MAE, RMSE and WAPE, then fits the transparent
recent-mean model on all rows. `model.json` includes date bounds, training row
count, source CSV SHA-256, evaluation details, and per-product history. The
forecast CSV is a demonstration baseline, not a production guarantee.

## SageMaker training and model registry

The CDK stack provisions an on-demand SageMaker execution role and a retained
Model Registry package group. It does not start a training job during stack
deployment. After infrastructure review/deployment and after producing a
verified CSV, upload the dataset and submit a CPU training job:

```powershell
aws s3 cp ..\data\processed\sales_daily.csv `
  s3://<InvoiceBucketName>/processed/sales/sales_daily.csv --profile capstone-dev
python src/run_sagemaker_training.py `
  --input-s3-uri s3://<InvoiceBucketName>/processed/sales/sales_daily.csv `
  --bucket <InvoiceBucketName> `
  --role-arn <SageMakerTrainingRoleArn> `
  --model-package-group <SalesForecastModelPackageGroup> `
  --region ap-southeast-2 --profile capstone-dev
```

The job uses one `ml.m5.large` CPU instance, stores artifacts under
`models/training-output/`, and registers a `PendingManualApproval` package.
Training is billable while the job runs. Inspect its source hash and holdout
metrics, then explicitly approve the package in SageMaker Model Registry.
No model version is auto-approved or deployed.

Managed MLflow is opt-in because its tracking server has ongoing charges. To
include it in the reviewed CDK stack, set `ENABLE_MLFLOW=true` before running
`python app.py` in `infra/`. Pass the resulting
`MlflowTrackingServerArn` to the training runner with
`--mlflow-tracking-server-arn <ARN>`. The AWS identity running the job must be
allowed to use SageMaker MLflow APIs. The runner logs training parameters,
metrics, model-package ARN, and artifact URI; it does not log invoice contents
or credentials.

After approval, a low-throughput Serverless Inference endpoint can be created,
called, and deleted explicitly:

```powershell
python src/manage_sagemaker_endpoint.py deploy `
  --role-arn <SageMakerTrainingRoleArn> `
  --model-package-arn <approved-model-package-arn> `
  --endpoint-name abos-dev-sales-forecast-demo
python src/manage_sagemaker_endpoint.py invoke `
  --endpoint-name abos-dev-sales-forecast-demo `
  --payload '<JSON with verified product history>'
python src/manage_sagemaker_endpoint.py delete `
  --endpoint-name abos-dev-sales-forecast-demo
```

Endpoint names must begin `abos-dev-sales-forecast` for the API Lambda's
restricted invoke permission. Serverless endpoints incur usage charges and
should be deleted when no longer needed. To connect an active endpoint to the
forecast API, configure `SAGEMAKER_ENDPOINT_NAME` in the stack context or
environment and deploy that reviewed configuration. With it unset, the API
continues using the same local baseline behavior.

## What requires real data or AWS access

Model training cannot be run meaningfully until sales are verified and mapped;
invoice counts, purchases, and an unverified inventory balance are not valid
substitutes. SageMaker jobs, Registry approval, endpoint provisioning, and
CloudWatch alarms require an authorized AWS session. None of these commands
were executed as part of local preparation.
