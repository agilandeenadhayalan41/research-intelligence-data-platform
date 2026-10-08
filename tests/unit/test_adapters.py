from io import BytesIO
import socket

import pytest

from research_platform.config import PlatformConfig
from research_platform.config.models import CloudConfig, StorageConfig, WarehouseConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.base import SourceConnector
from research_platform.sources.openalex import OpenAlexConnector
from research_platform.storage.base import ObjectStore
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.local import LocalObjectStore
from research_platform.warehouse.base import Warehouse
from research_platform.warehouse.bigquery import BigQueryWarehouse
from research_platform.warehouse.duckdb import DuckDBWarehouse
from research_platform.warehouse.postgres import PostgreSQLWarehouse
from tests.support.fake_gcs import FakeGCSClient


@pytest.mark.parametrize("contract", [SourceConnector, ObjectStore, Warehouse])
def test_contracts_cannot_be_instantiated(
    contract: type[SourceConnector] | type[ObjectStore] | type[Warehouse],
) -> None:
    with pytest.raises(TypeError, match="abstract"):
        contract()


def test_openalex_construction_has_no_implicit_network_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("connector construction must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_connection)
    connector = OpenAlexConnector()
    assert isinstance(connector, SourceConnector)
    assert connector.manifest_uri == "s3://openalex/data/jsonl/works/manifest.json"


def test_duckdb_warehouse_query_is_implemented(local_config: PlatformConfig) -> None:
    adapter = DuckDBWarehouse(local_config.warehouse)
    assert isinstance(adapter, Warehouse)
    try:
        table = adapter.query("SELECT 1 AS value")
        assert table.num_rows == 1
        assert table.column("value")[0].as_py() == 1
    finally:
        adapter.close()


def test_postgres_warehouse_constructs_without_a_connection(
    local_config: PlatformConfig,
) -> None:
    adapter = PostgreSQLWarehouse(local_config.warehouse)
    assert isinstance(adapter, Warehouse)


def test_bigquery_warehouse_is_concrete_with_injected_client() -> None:
    from tests.support.fake_bigquery import FakeBigQueryClient

    fake = FakeBigQueryClient()
    adapter = BigQueryWarehouse(
        WarehouseConfig(analytical="bigquery", bigquery_dataset="synthetic_dataset"),
        CloudConfig(project_id="synthetic-project"),
        client=fake,
    )
    assert isinstance(adapter, Warehouse)
    table = adapter.query("SELECT 1 AS value")
    assert table.num_rows == 1


def test_gcs_adapter_is_concrete_with_injected_client(
    provenance: IngestionProvenance,
) -> None:
    fake = FakeGCSClient()
    adapter = GCSObjectStore(
        StorageConfig(backend="gcs", bucket="synthetic-bucket"),
        CloudConfig(project_id="synthetic-project"),
        client=fake,
    )
    assert isinstance(adapter, ObjectStore)
    adapter.put_if_absent("synthetic/empty/source.bin", BytesIO(b""), provenance)
    with adapter.open("synthetic/empty/source.bin") as handle:
        assert handle.read() == b""


def test_gcs_construction_without_client_is_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("GCS construction must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_connection)
    adapter = GCSObjectStore(
        StorageConfig(backend="gcs", bucket="synthetic-bucket"),
        CloudConfig(project_id="synthetic-project"),
    )
    assert isinstance(adapter, ObjectStore)


def test_local_object_store_is_concrete(local_config: PlatformConfig) -> None:
    assert isinstance(LocalObjectStore(local_config.storage), ObjectStore)


def test_landing_requires_provenance(local_config: PlatformConfig) -> None:
    with pytest.raises(TypeError, match="provenance"):
        LocalObjectStore(local_config.storage).put_if_absent(
            "synthetic/empty/source.bin", BytesIO(b"")
        )
