"""Storage-independent Data Service domain layer (Step 18 / #26).

Consumer capabilities over quality-approved published Gold projections.
No HTTP framework, no raw SQL product endpoint, no cloud adapters.
"""

from research_platform.service.contracts import (
    JournalMetricsRequest,
    PageRequest,
    PublisherSummaryRequest,
    PublisherTopicAnalyticsRequest,
    ResearchDiscoveryRequest,
    WorkMetadataRequest,
)
from research_platform.service.models import (
    DATA_SERVICE_CONTRACT_VERSION,
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ConsistencySemantics,
    FreshnessMetadata,
    FreshnessStatus,
    JournalMetricsRecord,
    Page,
    PageInfo,
    PublisherSummaryRecord,
    PublisherTopicMetricRecord,
    ServiceError,
    ServiceErrorCode,
    ServiceErrorException,
    ServiceResponse,
    WorkMetadataRecord,
    WorkloadClass,
)
from research_platform.service.registry import (
    CapabilityContract,
    get_capability,
    load_capability_registry,
    serialize_capability_registry,
)
from research_platform.service.repository import (
    ConsumerDataRepository,
    InMemoryConsumerRepository,
    build_reference_fixture,
)
from research_platform.service.service import DataService, assert_no_raw_sql_consumer_methods
from research_platform.service.validation import validate_static_data_service_contracts

__all__ = [
    "DATA_SERVICE_CONTRACT_VERSION",
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "CapabilityContract",
    "ConsistencySemantics",
    "ConsumerDataRepository",
    "DataService",
    "FreshnessMetadata",
    "FreshnessStatus",
    "InMemoryConsumerRepository",
    "JournalMetricsRecord",
    "JournalMetricsRequest",
    "Page",
    "PageInfo",
    "PageRequest",
    "PublisherSummaryRecord",
    "PublisherSummaryRequest",
    "PublisherTopicAnalyticsRequest",
    "PublisherTopicMetricRecord",
    "ResearchDiscoveryRequest",
    "ServiceError",
    "ServiceErrorCode",
    "ServiceErrorException",
    "ServiceResponse",
    "WorkMetadataRecord",
    "WorkMetadataRequest",
    "WorkloadClass",
    "assert_no_raw_sql_consumer_methods",
    "build_reference_fixture",
    "get_capability",
    "load_capability_registry",
    "serialize_capability_registry",
    "validate_static_data_service_contracts",
]
