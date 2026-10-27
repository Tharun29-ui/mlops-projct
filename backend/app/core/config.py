from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Autonomous Business OS API"
    app_env: str = "local"
    api_prefix: str = "/api/v1"
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    aws_region: str = "ap-southeast-2"
    database_url: str = "sqlite:///../data/local.db"
    database_secret_arn: str | None = None
    database_host: str | None = None
    database_name: str = "autonomousbusinessos"
    business_data_table_name: str | None = None
    upload_dir: str = "../data/uploads"
    invoice_bucket_name: str | None = None
    extraction_jobs_table_name: str | None = None
    aws_extraction_enabled: bool = False
    invoice_sale_vendor: str = "Example Supplier"
    invoice_sale_invoice_number: str = "P-05"
    business_name: str | None = None
    business_gstin: str | None = None
    business_address: str | None = None
    business_state_code: str | None = None
    sagemaker_endpoint_name: str | None = None
    sagemaker_training_role_arn: str | None = None
    sagemaker_training_image: str | None = None
    max_upload_bytes: int = 15 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_cors_allowed_origins() -> list[str]:
    """Return the explicitly configured browser origins; never use a wildcard by default."""
    value = get_settings().cors_allowed_origins
    return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
