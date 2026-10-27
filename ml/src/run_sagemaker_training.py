"""Submit one CPU SageMaker training job and register its model for approval."""

from __future__ import annotations

import argparse
from pathlib import Path

import boto3
import sagemaker
from sagemaker.sklearn.estimator import SKLearn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-s3-uri", required=True, help="S3 object or prefix containing sales_daily.csv")
    parser.add_argument("--bucket", required=True, help="Project invoice/artifact bucket name")
    parser.add_argument("--role-arn", required=True, help="SageMaker execution role output by CDK")
    parser.add_argument("--model-package-group", required=True, help="Model package group name output by CDK")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--profile", default="capstone-dev")
    parser.add_argument("--instance-type", default="ml.m5.large")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--horizon-days", type=int, default=7)
    parser.add_argument("--mlflow-tracking-server-arn", help="Optional managed MLflow ARN output when enabled in CDK")
    args = parser.parse_args()

    boto_session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sm_session = sagemaker.Session(boto_session=boto_session, default_bucket=args.bucket)
    source_dir = Path(__file__).resolve().parent
    estimator = SKLearn(
        entry_point="train_sagemaker.py",
        source_dir=str(source_dir),
        role=args.role_arn,
        framework_version="1.2-1",
        py_version="py3",
        instance_type=args.instance_type,
        instance_count=1,
        output_path=f"s3://{args.bucket}/models/training-output/",
        hyperparameters={"window-days": args.window_days, "horizon-days": args.horizon_days},
        metric_definitions=[
            {"Name": "evaluation_mae", "Regex": r"evaluation_mae: ([0-9.eE+-]+)"},
            {"Name": "evaluation_rmse", "Regex": r"evaluation_rmse: ([0-9.eE+-]+)"},
            {"Name": "evaluation_wape", "Regex": r"evaluation_wape: ([0-9.eE+-]+)"},
        ],
        max_run=1800,
        sagemaker_session=sm_session,
        base_job_name="abos-sales-baseline",
        disable_profiler=True,
        debugger_hook_config=False,
    )
    estimator.fit({"train": args.input_s3_uri}, wait=True, logs="All")
    package = estimator.register(
        model_package_group_name=args.model_package_group,
        content_types=["application/json"],
        response_types=["application/json"],
        inference_instances=["ml.m5.large"],
        transform_instances=["ml.m5.large"],
        approval_status="PendingManualApproval",
        description=(
            "Recent-daily-mean baseline; includes chronological holdout metrics and source CSV SHA-256 in model.json. "
            "Review evaluation and source data before approval."
        ),
    )
    print(f"Training job: {estimator.latest_training_job.name}")
    print(f"Model package ARN: {package.model_package_arn}")
    print("Approval: PendingManualApproval; no endpoint was created.")
    if args.mlflow_tracking_server_arn:
        import mlflow

        mlflow.set_tracking_uri(args.mlflow_tracking_server_arn)
        mlflow.set_experiment("autonomous-business-os-sales-forecast")
        description = estimator.latest_training_job.describe()
        metrics = {
            row["MetricName"]: float(row["Value"])
            for row in description.get("FinalMetricDataList", [])
            if row.get("MetricName") in {"evaluation_mae", "evaluation_rmse", "evaluation_wape"}
        }
        with mlflow.start_run(run_name=estimator.latest_training_job.name):
            mlflow.log_params({
                "window_days": args.window_days,
                "horizon_days": args.horizon_days,
                "input_s3_uri": args.input_s3_uri,
                "model_package_group": args.model_package_group,
            })
            if metrics:
                mlflow.log_metrics(metrics)
            mlflow.set_tags({
                "training_job_name": estimator.latest_training_job.name,
                "model_package_arn": package.model_package_arn,
                "approval_status": "PendingManualApproval",
                "model_artifact_s3_uri": estimator.model_data,
            })
        print(f"Logged run to managed MLflow: {args.mlflow_tracking_server_arn}")


if __name__ == "__main__":
    main()
