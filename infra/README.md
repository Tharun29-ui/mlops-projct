# AWS infrastructure review

## Current stack scope

`AutonomousBusinessOsStack` currently defines these AWS components:

- private, encrypted, versioned S3 invoice bucket; supported objects under
  `incoming/` enter an SQS queue and dead-letter queue;
- a throttled Lambda worker starts Textract `StartExpenseAnalysis` jobs with an
  idempotency token derived from the S3 object version;
- Textract publishes completion to SNS, then SQS, and a second Lambda retrieves
  all result pages and writes raw plus normalized output under `processed/`;
  when AnalyzeExpense returns no item rows, that Lambda conditionally starts a
  `TABLES` analysis pass and merges the recognized table rows (including HSN/SAC
  and explicitly labeled invoice dates when printed clearly) before exposing
  the invoice for review. This extra Textract pass is limited to invoices whose
  first pass is missing a date, item rows, or stock-critical line-item fields;
- DynamoDB records extraction job state, with separate completion/upload DLQs.
- an on-demand DynamoDB table for invoice data, products, stock movements,
  forecast runs, and feedback;
- a FastAPI Lambda outside a VPC behind an HTTP API, with Cognito JWT
  authorization on `/api/*`; it accesses DynamoDB directly without a NAT or
  paid interface endpoint;
- a private S3 bucket served through CloudFront Origin Access Control, with
  HTTPS redirects, security headers, SPA route fallback, and a CDK bucket
  deployment that publishes the production frontend build and invalidates the
  CloudFront cache;
- a Cognito user pool and hosted sign-in domain. Public self-registration is
  disabled; create the first user as an administrator after deployment. The
  browser client uses authorization-code flow with PKCE and includes local and
  CloudFront callback URLs;
- a configurable monthly account-cost budget (default USD 20, aligned with the
  current AWS Settings project spend limit) publishes at 80%
  and 100% actual spend and 100% forecasted spend to an SNS topic; an optional
  email subscription is configured with `BUDGET_ALERT_EMAIL` or the
  `budgetAlertEmail` CDK context value;
- CloudWatch alarms for API 5xx responses, API/Textract Lambda errors, invoice
  queue age, and dead-letter messages publish to a separate
  operations SNS topic (the same optional email address is subscribed);
- a retained SageMaker Model Registry package group and an on-demand training
  execution role. No training job or endpoint is started by CDK. The role can
  read only verified sales data under `processed/sales/` and write artifacts
  under `models/`.

The budget is a notification threshold, not a spending cap. The trial stack has
email subscriptions on the budget and operations alarm topics; delivery begins
after the recipient confirms both AWS SNS messages. For a local synthesis with
the same subscriptions, set `BUDGET_ALERT_EMAIL` to the existing recipient or
another intended address. Do not commit a personal email address to the project.
Example synthesis configuration:

```powershell
$env:MONTHLY_BUDGET_USD = "20"
$env:BUDGET_ALERT_EMAIL = "developer@example.com"
python app.py
```

The application has no VPC, RDS instance, database secret, or interface
endpoint. DynamoDB uses on-demand capacity; requests and storage are usage
based. Other AWS services can still incur charges, and the USD 20 budget is
only an alert threshold. Point-in-time recovery is disabled for the business
data table in this demo template; export or back up important data before use.

Managed MLflow is disabled by default. Enabling it by setting
`$env:ENABLE_MLFLOW = "true"` before synthesis/deployment creates a continuously
running Small tracking server and S3 artifact storage; review regional pricing
before enabling. Its CloudFormation deletion policy is `Delete`, while the
versioned invoice/artifact bucket remains retained.

Original invoices and the job table use `RETAIN`; stack removal does not erase
those records. Review retention and privacy requirements before deploying.

## Review locally (no AWS resources created)

Install Python 3.10+ and then run from `infra/`:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Before synthesis, build the Linux Python 3.12 Lambda asset from the repository
root. The backend virtual environment must exist:

```powershell
.\backend\package_lambda.ps1
```

Then synthesize with the authenticated target account and region:

```powershell
$env:CDK_DEFAULT_ACCOUNT = (aws sts get-caller-identity --profile capstone-dev --query Account --output text)
$env:CDK_DEFAULT_REGION = "ap-southeast-2"
.\.venv\Scripts\python.exe app.py
Remove-Item Env:CDK_DEFAULT_ACCOUNT
Remove-Item Env:CDK_DEFAULT_REGION
```

Synthesis writes `cdk.out/` and does not create resources. The `capstone-dev`
profile is configured for the project's selected Region, `ap-southeast-2`, and
has been authenticated and verified with STS. The AWS Agent Toolkit is
installed; its Codex MCP server is configured in the user's global Codex
settings and uses this named profile. Restart Codex to load the new MCP server.
An attempt to attach `SignInLocalDevelopmentAccess` to the managed role was
denied by an AWS Organizations service control policy; do not attempt to change
the AWS-managed project policy. Do not paste credentials into this project or
chat.

Review the synthesized template for IAM scope, retention, queue behavior, and
region before any deployment. Do not place credentials in the repository or
chat.

## Frontend publishing

The stack outputs `FrontendUrl`, `FrontendBucketName`, and
`FrontendDistributionId`. After deployment, create `frontend/.env.production`
from those outputs and the Cognito outputs:

```dotenv
VITE_API_BASE_URL=https://<BusinessApiUrl-host>/api/v1
VITE_COGNITO_DOMAIN=https://<CognitoHostedUiBaseUrl-host>
VITE_COGNITO_CLIENT_ID=<CognitoAppClientId>
VITE_COGNITO_REDIRECT_URI=<FrontendUrl>/auth/callback
VITE_COGNITO_LOGOUT_URI=<FrontendUrl>
```

Build the frontend with the matching stack outputs before synthesizing and
deploying. `BucketDeployment` packages `frontend/dist/`, copies it into the
private hosting bucket, and invalidates CloudFront during the stack deployment.

```powershell
cd frontend
npm ci
npm run build
```

Use the existing `capstone-dev` profile and selected Region. For this trial the
CDK environment context is `trial`; the deployment stack is
`AutonomousBusinessOS-trial`. Keep `frontend/.env.production` out of version
control; it contains public client configuration, not AWS credentials. Create
the first Cognito user through the AWS console because public self-registration
is disabled.

To repeat the deployment from the repository root after refreshing the frontend
configuration and building `frontend/dist/`, use the project's CDK environment:

```powershell
cd infra
$env:BUDGET_ALERT_EMAIL = "developer@example.com"
npx --yes --package aws-cdk cdk synth --app ".\.venv\Scripts\python.exe app.py" `
  --profile capstone-dev --region ap-southeast-2 -c environment=trial `
  --output cdk.trial.out
npx --yes --package aws-cdk cdk deploy AutonomousBusinessOS-trial `
  --app cdk.trial.out --profile capstone-dev --region ap-southeast-2 `
  --require-approval never
```

## Model training and monitoring

The SageMaker execution role and model package group are created by the stack.
The on-demand CPU training, Registry approval, Serverless Inference endpoint,
and cleanup commands are documented in `../ml/README.md`. The endpoint name
must begin `abos-trial-sales-forecast` to match the API Lambda's scoped
`InvokeEndpoint` policy. To make forecast uploads call the endpoint, synthesize
and deploy with `SAGEMAKER_ENDPOINT_NAME` or CDK context
`sagemakerEndpointName` set to that deployed endpoint name. With no endpoint
name configured, the API uses the local baseline. A live SageMaker endpoint
has ongoing compute charges; keep it disabled until verified sales history
justifies an inference trial.

CloudWatch alarms publish to `OperationsAlarmTopicArn`; configure
`BUDGET_ALERT_EMAIL` or `budgetAlertEmail` before deployment, then confirm the
subscription message. The alarms are operational notifications, not automatic
remediation or spending limits. The three application Lambda log groups have
explicit one-week retention and are deleted with the stack.

## Still pending before deployment

The full app template has been synthesized locally for the project's selected
Region, `ap-southeast-2`, but has not been deployed. On 2026-10-16, the project
plan reported `PAID` and active; a read-only Textract `ListAdapters` request
succeeded with zero adapters. Before bootstrap, no application resources were
present in the selected Region. Training, model registration, inference, and
CloudWatch alarms are prepared but still require real AWS execution and review.
Managed MLflow is optional and disabled by default due to its ongoing cost. A
valid daily sales dataset is required to train and try a meaningful forecast
model. Automated deployment approvals and an unattended sales-data pipeline
remain outside this capstone stack. Review the entire generated template,
access policies, resource cost, retention, and Cognito user provisioning before
any application deployment.

The standard `CDKToolkit` bootstrap stack is deployed in `ap-southeast-2` with
termination protection enabled; the application stack is not deployed. The
latest local synthesis was compared with AWS using `cdk diff`. It published the
current Lambda asset to the CDK staging bucket and created a read-only
CloudFormation change set. The diff reports a first-time application stack
creation; no application resources were deployed. Since then, the local changes
gave all three application Lambda functions explicit log groups with one-week
retention and removed a CloudFront TLS setting that AWS ignores with the default
certificate. The domainless CloudFront URL uses
the default certificate, which fixes the minimum TLS protocol at TLSv1; a TLS
1.2 minimum requires an alternate domain and matching certificate. The current
CDK bootstrap uses its default CloudFormation execution policy
`AdministratorAccess`, bounded by the project's AWS-managed SCP. Do not attempt
to bypass that SCP. The revised estimate and resource inventory are in
`../docs/aws-cost-review.md`. The USD 20 project limit and CDK budget are alerts,
not hard caps. CLI authentication works through `aws login`; attaching extra
permissions to a managed role is not needed and was denied by project policy.

To repeat the infrastructure diff after reviewing or changing the template,
run the CDK CLI against the local cloud assembly:

```powershell
npx --yes --package aws-cdk cdk diff AutonomousBusinessOS-dev --app cdk.out --profile capstone-dev --region ap-southeast-2
```

The application stack is a first-time create and will provision chargeable
resources. Run `cdk deploy` only after reviewing the full resource and cost
scope and receiving explicit approval for deployment.
