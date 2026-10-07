from io import BytesIO
import socket

import pytest

from research_platform.config import PlatformConfig
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


def test_warehouse_skeletons_do_not_execute_queries(local_config: PlatformConfig) -> None:
    adapters = [
        PostgreSQLWarehouse(local_config.warehouse),
        DuckDBWarehouse(local_config.warehouse),
        BigQueryWarehouse(local_config.warehouse, local_config.cloud),
    ]
    for adapter in adapters:
        assert isinstance(adapter, Warehouse)
        with pytest.raises(NotImplementedError, match="later phase"):
            adapter.query("SELECT :value", {"value": 1})


def test_gcs_skeleton_does_not_read_or_write(
    local_config: PlatformConfig, provenance: IngestionProvenance
) -> None:
    adapter = GCSObjectStore(local_config.storage, local_config.cloud)
    assert isinstance(adapter, ObjectStore)
    with pytest.raises(NotImplementedError, match="later phase"):
        adapter.put_if_absent("synthetic/empty/source.bin", BytesIO(b""), provenance)
    with pytest.raises(NotImplementedError, match="later phase"):
        adapter.open("synthetic/empty/source.bin")


def test_local_object_store_is_concrete(local_config: PlatformConfig) -> None:
    assert isinstance(LocalObjectStore(local_config.storage), ObjectStore)


def test_landing_requires_provenance(local_config: PlatformConfig) -> None:
    with pytest.raises(TypeError, match="provenance"):
        LocalObjectStore(local_config.storage).put_if_absent(
            "synthetic/empty/source.bin", BytesIO(b"")
        )
