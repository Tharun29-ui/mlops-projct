# Autonomous Business OS

Invoice capture and review, purchase/sale inventory, vendor/customer payment history, and one monthly gross-profit forecast.

## Current forecast

The replacement pipeline is **profit per bill -> monthly profit total -> monthly Ridge Regression forecast**. It replaces the earlier product-demand, selling-price and purchase-cost forecast screens and active packaged weights. See [the monthly profit guide](docs/monthly-profit-guide.md).

- Real and synthetic bills are separate workspaces.
- Bill profit uses net sales minus attributable product/service cost. It is not sales minus all purchases made in the same month.
- An explicit recorded cost or an exactly matched earlier purchase rate can populate bill profit. A dated purchase rate is a cost estimate, not FIFO valuation.
- Missing real costs stay unknown. Enter a verified profit for each affected bill, or import the filled bill-profit CSV. Confirmations are stored separately with fingerprints and history; stock, approvals and payments are unaffected.
- Every included bill in a calendar month must have a profit before that month trains the model. Missing calendar months are not invented as zero profit.
- Two usable monthly totals can fit a basic trend; short histories are exploratory. With at least 18 months, the same Ridge model uses annual seasonality and month length.
- Gross profit excludes operating expenses and tax. Forecast scope is the included recorded bills; selected real examples do not establish total business coverage.
- The synthetic ledger has 730 sales bills and 24 monthly totals. Model development used a chronological backtest; these are development results, not an untouched external validation or real-business accuracy.

The old SageMaker job and historical evidence remain available as prior-release history. They do not train or validate the new monthly-profit model. This replacement fits portable Python models in the existing API Lambda and requires no additional paid training job or persistent endpoint.

## Architecture

React frontend -> CloudFront/private S3 -> Cognito-authorized API Gateway -> FastAPI Lambda -> DynamoDB business records.

Invoice originals -> private S3 -> SQS/Lambda -> Textract -> SNS/SQS completion -> normalized review fields. Approval posts stock movements. Purchase goods add stock; sales deduct stock; services do not change physical stock. Sales receipts and vendor/customer payment ledgers are available. GST reports and GST tax-invoice issuance are excluded.

The active monthly-profit API is `GET /api/v1/forecasts/profit?source=real|synthetic&horizon=1|3|6&start_month=YYYY-MM`. Real profit confirmation/import uses `POST /api/v1/forecasts/profit/confirmations`. Retired model endpoints are no longer registered.

## Run locally

Use the existing backend virtual environment, apply migrations, and run `uvicorn app.main:app --reload` from `backend/`. Run `npm run dev` from `frontend/`. The local API is intended only for local development. The hosted deployment uses Cognito authentication.

Backend tests: from `backend/`, run `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Frontend checks: `npm test` and `npm run build`. Tests for retired forecast endpoints are archived with their source; invoice, stock, receipt and payment regression tests remain active.

## Data and deployment

`data/sample_invoices` contains real sales PDFs. `data/sales_test/purchase` contains real purchase images. Do not swap their directions or treat quotations as completed sales. Source documents, private values and credentials must stay out of public Git history.

The selected AWS Region is Sydney, `ap-southeast-2`. The site is https://YOUR_DISTRIBUTION.cloudfront.net Local changes must be verified and deployed before describing them as live. CDK manages the existing stack. The USD20 budget alert is a notification, not a hard cap; AWS Settings spend controls are separate. Preserve infrastructure, stored invoice files and historical evidence when replacing application models.

## Public checkout setup and privacy

This repository is a sanitized source release. Production credentials, real invoice
PDFs/images, OCR extracts, real transaction ledgers, deployment outputs and screenshots
are excluded. Configure your own local .env files and AWS resources. The URLs above
are deployment placeholders.

Before starting the API, copy the fictional monthly-profit seed into the ignored
runtime-data directory. Its real sales and purchases lists are empty; your approved
local business invoices are read from your own database.

```powershell
New-Item -ItemType Directory -Force backend/app/data
Copy-Item ml/datasets/synthetic-model-demo/monthly-profit-ledger.json backend/app/data/bill-profit-ledger.json
cd backend
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In another terminal, run `npm ci` and `npm run dev` from frontend/. Keep the local
unauthenticated development API bound to localhost. The AWS deployment uses Cognito.
The synthetic monthly seed contains 730 generated sales bills and 255 generated
purchase bills. It is never a source of real-business accuracy claims.

The inference-time panel measures warm, already-fitted one-month predictions separately
from data preparation and the browser request round trip. See docs/monthly-profit-guide.md.

Older demand, selling-price and purchase-cost training scripts and results retained
under ml/ and scripts/ describe a historical release. They are not the active monthly
gross-profit model. Do not use their evaluation numbers to describe the current model.

The public backend suite uses isolated fictional bills for API confirmation tests.
The source-corpus audit is skipped when the intentionally excluded private real
ledger is absent; this does not claim that real business forecasting was validated.
