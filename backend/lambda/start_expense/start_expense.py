"""Start asynchronous Textract AnalyzeExpense jobs for S3 upload events."""

from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError


textract = boto3.client("textract")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["JOB_TABLE_NAME"])
notification_topic_arn = os.environ["TEXTRACT_TOPIC_ARN"]
textract_role_arn = os.environ["TEXTRACT_ROLE_ARN"]


def _idempotency_key(bucket: str, key: str, version: str) -> str:
    raw = f"{bucket}\n{key}\n{version}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def handler(event: dict, _context: object) -> dict[str, int]:
    started = 0
    for record in event.get("Records", []):
        message = json.loads(record["body"])
        for s3_record in message.get("Records", []):
            if s3_record.get("eventSource") != "aws:s3":
                continue
            bucket = s3_record["s3"]["bucket"]["name"]
            object_info = s3_record["s3"]["object"]
            key = unquote_plus(object_info["key"])
            version = object_info.get("versionId") or object_info.get("eTag", "").strip('"')
            token = _idempotency_key(bucket, key, version)

            existing = table.get_item(
                Key={"idempotency_key": token},
                ConsistentRead=True,
            ).get("Item")
            if existing and existing.get("job_id"):
                continue

            document_location = {"Bucket": bucket, "Name": key}
            if object_info.get("versionId"):
                document_location["Version"] = object_info["versionId"]
            result = textract.start_expense_analysis(
                DocumentLocation={"S3Object": document_location},
                ClientRequestToken=token,
                JobTag=token,
                NotificationChannel={
                    "SNSTopicArn": notification_topic_arn,
                    "RoleArn": textract_role_arn,
                },
            )
            try:
                table.put_item(
                    Item={
                        "idempotency_key": token,
                        "job_id": result["JobId"],
                        "bucket": bucket,
                        "object_key": key,
                        "version_id": version,
                        "status": "IN_PROGRESS",
                    },
                    ConditionExpression="attribute_not_exists(idempotency_key)",
                )
            except ClientError as error:
                if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
            started += 1

    print(json.dumps({"started_jobs": started}))
    return {"started_jobs": started}
