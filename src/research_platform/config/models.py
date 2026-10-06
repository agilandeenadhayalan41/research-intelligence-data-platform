"""Non-secret configuration; credentials belong in the process environment."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class StorageConfig(SettingsModel):
    backend: Literal["local", "gcs"] = "local"
    landing_path: Path = Path("data/landing")
    bucket: str | None = None

    @model_validator(mode="after")
    def require_bucket(self) -> Self:
        if self.backend == "gcs" and not self.bucket:
            raise ValueError("GCS storage requires a bucket")
        return self


class WarehouseConfig(SettingsModel):
    transactional: Literal["postgres"] = "postgres"
    analytical: Literal["duckdb", "bigquery"] = "duckdb"
    postgres_dsn_env: str = "POSTGRES_DSN"
    duckdb_path: str = ":memory:"
    bigquery_dataset: str | None = None


class CloudConfig(SettingsModel):
    project_id: str | None = None


class PlatformConfig(SettingsModel):
    environment: Literal["local", "gcp-sandbox", "dev", "qa", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    source: Literal["openalex"] = "openalex"
    storage: StorageConfig = StorageConfig()
    warehouse: WarehouseConfig = WarehouseConfig()
    cloud: CloudConfig = CloudConfig()

    @model_validator(mode="after")
    def require_cloud_identifiers(self) -> Self:
        if self.storage.backend == "gcs" or self.warehouse.analytical == "bigquery":
            if not self.cloud.project_id:
                raise ValueError("Cloud adapters require an environment-supplied project ID")
        if self.warehouse.analytical == "bigquery" and not self.warehouse.bigquery_dataset:
            raise ValueError("BigQuery requires a dataset")
        return self
