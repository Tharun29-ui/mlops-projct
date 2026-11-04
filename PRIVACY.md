# Public release privacy policy

This source release excludes real invoices, OCR extracts, real transaction datasets,
private runtime ledgers, screenshots, student records, backups, local databases,
environment files, AWS output/context files, credentials and certificates.

The monthly-profit seed under ml/datasets/synthetic-model-demo has an empty real
branch and only explicitly generated synthetic sales and purchase bills. Older
synthetic examples use generic business placeholders. Never combine simulated
evaluation with real-business accuracy claims.

Production resource identifiers and business contact details in source examples
are replaced with placeholders. Configure actual values locally or in managed secrets.
Historical one-off deployment, reset and data-repair scripts are not distributed.
