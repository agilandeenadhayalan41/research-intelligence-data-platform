from io import BytesIO

import pytest

from research_platform.config import PlatformConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.base import SourceAsset, SourceConnector
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


def test_openalex_remains_a_skeleton() -> None:
    connector = OpenAlexConnector()
    assert isinstance(connector, SourceConnector)
    asset = SourceAsset("synthetic", "empty", "https://example.org/synthetic/empty")
    with pytest.raises(NotImplementedError, match="discovery"):
        connector.discover()
    with pytest.raises(NotImplementedError, match="retrieval"):
        connector.fetch(asset)


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


def test_storage_skeletons_do_not_read_or_write(
    local_config: PlatformConfig, provenance: IngestionProvenance
) -> None:
    adapters = [
        LocalObjectStore(local_config.storage),
        GCSObjectStore(local_config.storage, local_config.cloud),
    ]
    for adapter in adapters:
        assert isinstance(adapter, ObjectStore)
        with pytest.raises(NotImplementedError, match="later phase"):
            adapter.put_if_absent("synthetic/empty", BytesIO(b""), provenance)
        with pytest.raises(NotImplementedError, match="later phase"):
            adapter.open("synthetic/empty")


def test_landing_requires_provenance(local_config: PlatformConfig) -> None:
    with pytest.raises(TypeError, match="provenance"):
        LocalObjectStore(local_config.storage).put_if_absent("synthetic/empty", BytesIO(b""))
