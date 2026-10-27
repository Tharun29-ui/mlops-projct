import os

import aws_cdk as cdk

from autonomous_business_os_stack import AutonomousBusinessOsStack


app = cdk.App(outdir=os.getenv("CDK_OUTDIR", "cdk.out"))
environment_name = app.node.try_get_context("environment") or os.getenv("ABOS_ENVIRONMENT") or "dev"
stack_name = f"AutonomousBusinessOS-{environment_name}"
monthly_budget_usd = float(
    app.node.try_get_context("monthlyBudgetUsd") or os.getenv("MONTHLY_BUDGET_USD", "20")
)
budget_alert_email = app.node.try_get_context("budgetAlertEmail") or os.getenv("BUDGET_ALERT_EMAIL")
invoice_sale_vendor = app.node.try_get_context("invoiceSaleVendor") or os.getenv("INVOICE_SALE_VENDOR", "Example Supplier")
invoice_sale_invoice_number = app.node.try_get_context("invoiceSaleInvoiceNumber") or os.getenv("INVOICE_SALE_INVOICE_NUMBER", "P-05")
business_name = app.node.try_get_context("businessName") or os.getenv("BUSINESS_NAME", "")
business_gstin = app.node.try_get_context("businessGstin") or os.getenv("BUSINESS_GSTIN", "")
business_address = app.node.try_get_context("businessAddress") or os.getenv("BUSINESS_ADDRESS", "")

AutonomousBusinessOsStack(
    app,
    stack_name,
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "ap-southeast-2"),
    ),
    environment_name=environment_name,
    monthly_budget_usd=monthly_budget_usd,
    budget_alert_email=budget_alert_email,
    invoice_sale_vendor=invoice_sale_vendor,
    invoice_sale_invoice_number=invoice_sale_invoice_number,
    business_name=business_name,
    business_gstin=business_gstin,
    business_address=business_address,
)

app.synth()
