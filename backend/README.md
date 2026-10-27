# Backend

FastAPI application with SQLite local persistence, invoice review and correction,
POS/GST invoices, inventory movements and serial/warranty tracking, a GST
management summary, baseline forecasting, and feedback. Uploads are fingerprinted with SHA-256: an identical file is flagged
before storage or Textract processing, and the reviewer can confirm an
intentional second copy. Once vendor and invoice number are available, the
review queue also warns about likely repeated invoice records. Textract field
confidence is retained for header fields and line items so low-confidence or
missing values can be checked before approval. Cloud runtime persistence uses
DynamoDB; local persistence uses SQLite.

## Run locally

Run these commands from `backend/` in PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

The API no longer creates database tables at startup. For a workspace that
already has the tables created by the earlier local version, mark that existing
schema at the initial migration once (this records the migration version and
does not change invoice data):

```powershell
alembic stamp head
```

Use `alembic upgrade head` for a new database and after future schema changes.
Create new revisions with `alembic revision --autogenerate -m "describe change"`
and review the generated migration before applying it.

Open `http://127.0.0.1:8000/docs`. SQLite data is saved to `../data/local.db`;
uploaded invoice files go to `../data/uploads/`. Both are ignored by Git.

## API workflows

- `POST /api/v1/invoices/upload` accepts PDF, JPEG, and PNG (15 MB maximum).
- Uploading a byte-identical file returns HTTP 409 with the matching filename. To confirm an intentional second copy, repeat with `?allow_duplicate=true`; the record remains visibly flagged.
- `GET /api/v1/invoices` includes `duplicate_matches`, Textract `field_confidence` values from 0 to 1, and line confidence values. The review queue flags missing key fields or confidence below 85%.
- `GET /api/v1/invoices`, `GET /api/v1/invoices/{id}`, and `GET /api/v1/invoices/{id}/document` list, inspect, and open source files.
- `PATCH /api/v1/invoices/{id}/correction` saves checked invoice header fields and line items.
- `PATCH /api/v1/invoices/{id}/review` approves or rejects; approval requires vendor, invoice number, date, and grand total.
- `GET /api/v1/inventory` and `POST /api/v1/inventory` list or create products.
- `PATCH /api/v1/inventory/{id}/pricing` saves optional per-product minimum and target profit margins. Suggested sell prices use average unit cost and true margin (`cost / (1 - margin)`); sale invoice review warns below the configured minimum, or below cost when no minimum is set.
- `POST /api/v1/inventory/{id}/movements` adjusts stock; `GET /api/v1/inventory/{id}/movements` returns movement history.
- `GET /api/v1/inventory/serial-assets` lists captured serial numbers, their stock/sold status, and warranty dates.
- `POST /api/v1/invoices/sales` creates an approved POS sale, reduces inventory, records payment received, and returns a generated GST invoice through `GET /api/v1/invoices/{id}/gst-pdf`. Configure `BUSINESS_NAME`, `BUSINESS_GSTIN`, and `BUSINESS_ADDRESS` before issuing sales invoices.
- `POST /api/v1/invoices/{invoice_id}/payments` records a later payment against an approved invoice, prevents overpayment, and updates the outstanding balance and payment history. The GST Reports screen exposes outstanding approved balances and payment entry.
- `GET /api/v1/reports/gst?from=YYYY-MM-DD&to=YYYY-MM-DD` summarizes approved sales and purchases and sale values by HSN/SAC. It is a management report, not a statutory filing export.
- `POST /api/v1/forecasts/import` validates daily sales CSV, evaluates a recent-mean baseline on a chronological holdout, and stores a seven-day forecast.
- `GET /api/v1/forecasts/latest` reads the latest run; `POST /api/v1/forecasts/{run_id}/feedback` records observed actuals.
- `POST /api/v1/invoices/validate` validates a supplied invoice payload and returns warnings.

Start the sales file from `../data/manifests/sales_daily_template.csv`. Include
one row per product per calendar day; use zero-sale rows only when the source is
known to be complete. Supplier purchase invoices must not be treated as sales.
The forecast pipeline rejects gaps, duplicate dates, negative quantities, and
series shorter than four days.

The local API has no authentication and must not be exposed publicly. The CDK
stack configures Cognito authorization, S3 invoice upload, DynamoDB, Textract
result access, and the private Lambda/API integration. If a reviewed
SageMaker Serverless endpoint is configured using `SAGEMAKER_ENDPOINT_NAME`,
forecast CSV uploads invoke it for predictions; otherwise the API uses the
local recent-mean baseline. The SageMaker endpoint is optional and is never
created automatically by the API or CDK stack.
