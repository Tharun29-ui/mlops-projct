"""Create, call, or remove a low-throughput Serverless Inference endpoint."""

from __future__ import annotations

import argparse
import json

import boto3
from sagemaker.model import ModelPackage
from sagemaker.serverless import ServerlessInferenceConfig
from sagemaker.session import Session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "invoke", "delete"])
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--profile", default="capstone-dev")
    parser.add_argument("--endpoint-name", default="abos-dev-sales-forecast-demo")
    parser.add_argument("--role-arn", help="SageMakerTrainingRoleArn stack output, required to deploy")
    parser.add_argument("--model-package-arn", help="Approved package ARN, required to deploy")
    parser.add_argument(
        "--payload",
        help='Required for invoke; JSON such as {"series":[{"product_id":"SKU","last_observed_date":"YYYY-MM-DD","recent_quantities":[...]}]} using verified daily sales',
    )
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)

    if args.action == "deploy":
        if not args.role_arn or not args.model_package_arn:
            parser.error("deploy requires --role-arn and an approved --model-package-arn")
        model = ModelPackage(
            role=args.role_arn,
            model_package_arn=args.model_package_arn,
            sagemaker_session=Session(boto_session=session),
        )
        predictor = model.deploy(
            endpoint_name=args.endpoint_name,
            serverless_inference_config=ServerlessInferenceConfig(
                memory_size_in_mb=1024,
                max_concurrency=2,
            ),
            wait=True,
        )
        print(f"Serverless endpoint is ready: {predictor.endpoint_name}")
        return

    if args.action == "invoke":
        if not args.payload:
            parser.error("invoke requires --payload with verified product history")
        response = session.client("sagemaker-runtime").invoke_endpoint(
            EndpointName=args.endpoint_name,
            ContentType="application/json",
            Accept="application/json",
            Body=args.payload.encode("utf-8"),
        )
        print(response["Body"].read().decode("utf-8"))
        return

    client = session.client("sagemaker")
    endpoint = client.describe_endpoint(EndpointName=args.endpoint_name)
    config_name = endpoint["EndpointConfigName"]
    config = client.describe_endpoint_config(EndpointConfigName=config_name)
    model_names = [variant["ModelName"] for variant in config.get("ProductionVariants", [])]
    client.delete_endpoint(EndpointName=args.endpoint_name)
    client.get_waiter("endpoint_deleted").wait(EndpointName=args.endpoint_name)
    client.delete_endpoint_config(EndpointConfigName=config_name)
    for model_name in model_names:
        client.delete_model(ModelName=model_name)
    print(f"Endpoint, endpoint config, and model removed: {args.endpoint_name}")


if __name__ == "__main__":
    main()
