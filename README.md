# Autonomous Business OS

AWS-based invoice automation, inventory management and ML sales planning.
This repository is a sanitized source release: real invoices, customer/supplier
records, production settings, credentials, and real-data model bundles are not included.

## Features

- PDF/image invoice intake and Amazon Textract integration
- Human review, duplicate checks and purchase/sale classification
- Stock updates on approval, inventory search and movement history
- Sales, receipts, payment tracking and margin controls
- Demand, selling-price and purchase-cost model evaluation
- Chronological validation, held-out errors, baseline comparisons and portable JSON inference

## Architecture

React frontend → CloudFront/S3 → Cognito → API Gateway → FastAPI on Lambda.
DynamoDB stores business records. S3 stores uploaded documents. SQS/SNS and
Textract support extraction, and CloudWatch supports operational monitoring.
CDK defines infrastructure in ap-southeast-2. No EC2 instance or active
SageMaker endpoint is required. Monthly training runs offline; portable inference
runs in Lambda. Historical optional SageMaker code is retained but is not an
active or validated deployment path.

## Run locally

Use Python 3.12 and Node.js compatible with the frontend dependencies.

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

In another terminal:

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

Open http://localhost:5173. The local API is unauthenticated: keep it on localhost.
Fill deployment-specific settings locally; do not commit .env files or cloud outputs.

## Synthetic ML demonstration

The only supplied invoice dataset is explicitly fictional and separated under
`ml/datasets/synthetic-model-demo/purchase` and `sales`. It has 24 simulated months,
six products, complete CSV histories and ten illustrative invoice PDFs per direction.

Install offline dependencies in the backend virtual environment, then run:

```powershell
pip install -r ml/requirements-training.txt
.\ml\datasets\synthetic-model-demo\RUN.ps1
```

Read `ml/datasets/synthetic-model-demo/START-HERE.md` and `results/evaluation.json`.
Synthetic test WAPE: demand 7.340%, selling price 0.195%, purchase cost 0.242%.
These are rolling one-month test errors on simulated patterns, not real-business
accuracy. Earlier private real-data demand evaluation recorded 89.745% WAPE and
failed its acceptance gate. Do not combine those evaluations or claim synthetic
results establish real forecasting reliability. Complete real sales history is needed.
The public synthetic demo is a local evaluation; it does not add a live website mode.

## Deployment

See `infra/README.md`, `backend/package_lambda.ps1` and the CDK source. Configure
your own Cognito, buckets, table and API values. Cost depends on usage; budget
alerts are not hard caps. No account IDs, live endpoints or production credentials
are required in source control.

## Privacy

See `PRIVACY.md`. Real customer data and deployment-specific files must stay private.
