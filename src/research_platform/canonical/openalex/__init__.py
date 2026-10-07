"""OpenAlex-specific portable canonical model contracts."""

from research_platform.canonical.openalex.errors import (
    CanonicalModelError,
    IdentifierError,
    MappingError,
)
from research_platform.canonical.openalex.field_catalog import FIELD_CATALOG, FieldSupport
from research_platform.canonical.openalex.identifiers import (
    normalize_doi,
    normalize_issn,
    normalize_keyword_id,
    normalize_openalex_id,
    normalize_orcid,
    normalize_ror,
    openalex_url,
)
from research_platform.canonical.openalex.mapping import map_openalex_work
from research_platform.canonical.openalex.models import (
    ArrayPresence,
    Author,
    CanonicalActivityState,
    CanonicalLineage,
    CanonicalWorkBundle,
    Funder,
    Institution,
    LocationOrigin,
    Publisher,
    ReferenceStatus,
    Source,
    Topic,
    Work,
    WorkAuthor,
    WorkAuthorInstitution,
    WorkGrant,
    WorkKeyword,
    WorkLocation,
    WorkMesh,
    WorkReference,
    WorkTopic,
)
from research_platform.canonical.openalex.reconciliation import (
    EntityReconciliation,
    merge_shared_entity,
    reconcile_shared_entity,
)
from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS
from research_platform.canonical.openalex.versioning import (
    VersionComparison,
    compare_work_versions,
)

__all__ = [
    "ArrayPresence",
    "Author",
    "CANONICAL_SCHEMAS",
    "CanonicalActivityState",
    "CanonicalLineage",
    "CanonicalModelError",
    "CanonicalWorkBundle",
    "EntityReconciliation",
    "FIELD_CATALOG",
    "FieldSupport",
    "Funder",
    "IdentifierError",
    "Institution",
    "LocationOrigin",
    "MappingError",
    "Publisher",
    "ReferenceStatus",
    "Source",
    "Topic",
    "VersionComparison",
    "Work",
    "WorkAuthor",
    "WorkAuthorInstitution",
    "WorkGrant",
    "WorkKeyword",
    "WorkLocation",
    "WorkMesh",
    "WorkReference",
    "WorkTopic",
    "compare_work_versions",
    "map_openalex_work",
    "merge_shared_entity",
    "normalize_doi",
    "normalize_issn",
    "normalize_keyword_id",
    "normalize_openalex_id",
    "normalize_orcid",
    "normalize_ror",
    "openalex_url",
    "reconcile_shared_entity",
]
