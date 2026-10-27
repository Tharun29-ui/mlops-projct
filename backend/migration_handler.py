"""One-off CloudFormation custom-resource handler that applies Alembic migrations."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: object) -> dict:
    request_type = event.get("RequestType")
    physical_id = event.get("PhysicalResourceId", "autonomous-business-os-schema")
    if request_type == "Delete":
        return {"PhysicalResourceId": physical_id, "Data": {"revision": "retained"}}

    ini_path = Path(__file__).resolve().parent / "alembic.ini"
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(ini_path.parent / "migrations"))
    command.upgrade(config, "head")
    logger.info("Business database schema is at the latest Alembic revision")
    return {"PhysicalResourceId": physical_id, "Data": {"revision": "head"}}
