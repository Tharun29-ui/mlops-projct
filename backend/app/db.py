from pathlib import Path
import json

import boto3
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine():
    settings = get_settings()
    url = settings.database_url
    if settings.database_secret_arn:
        secret = boto3.client("secretsmanager", region_name=settings.aws_region).get_secret_value(
            SecretId=settings.database_secret_arn
        )["SecretString"]
        credentials = json.loads(secret)
        url = URL.create(
            "postgresql+psycopg",
            username=credentials["username"],
            password=credentials["password"],
            host=credentials.get("host") or settings.database_host,
            port=int(credentials.get("port", 5432)),
            database=credentials.get("dbname") or settings.database_name,
            query={"sslmode": "require"},
        )
    if not isinstance(url, str):
        return create_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite:"):
        db_path = url.removeprefix("sqlite:///")
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


_settings = get_settings()
engine = None if _settings.business_data_table_name else _engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def get_db():
    if SessionLocal is None:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
