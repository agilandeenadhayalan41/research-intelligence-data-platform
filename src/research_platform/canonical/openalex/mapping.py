"""Map one synthetic/public OpenAlex Work JSON object to canonical tables."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping

from research_platform.canonical.openalex.errors import IdentifierError, MappingError
from research_platform.canonical.openalex.identifiers import (
    normalize_doi,
    normalize_issn,
    normalize_keyword_id,
    normalize_openalex_id,
    normalize_orcid,
    normalize_ror,
    openalex_url,
    try_normalize_openalex_id,
)
from research_platform.canonical.openalex.models import (
    ArrayPresence,
    Author,
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

_EXACT_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _presence(record: Mapping[str, object], key: str) -> ArrayPresence:
    if key not in record:
        return ArrayPresence.MISSING
    value = record[key]
    if value is None:
        return ArrayPresence.NULL
    if not isinstance(value, list):
        raise MappingError(f"{key} must be an array, null, or absent")
    if len(value) == 0:
        return ArrayPresence.EMPTY
    return ArrayPresence.PRESENT


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MappingError("expected string or null")
    text = value.strip()
    return text or None


def _parse_date(value: object | None, *, field_name: str) -> date | None:
    """Accept only exact ``YYYY-MM-DD`` calendar dates (or Python ``date``).

    Timestamps, truncated prefixes, and trailing garbage are rejected. OpenAlex
    Work ``publication_date`` / ``created_date`` / ``updated_date`` are modeled
    as calendar dates in this contract.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        raise MappingError(f"{field_name} must be YYYY-MM-DD, not a timestamp")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{field_name} must be exact YYYY-MM-DD or null")
    text = value.strip()
    if not _EXACT_DATE.fullmatch(text):
        raise MappingError(f"{field_name} must be exact YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise MappingError(f"{field_name} must be a real calendar date") from error
    if parsed.isoformat() != text:
        raise MappingError(f"{field_name} must be exact YYYY-MM-DD")
    return parsed


def _optional_int(value: object | None, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MappingError(f"{field_name} must be an integer or null")
    return value


def _optional_bool(value: object | None, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise MappingError(f"{field_name} must be a boolean or null")
    return value


def _optional_float(value: object | None, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MappingError(f"{field_name} must be a number or null")
    return float(value)


def map_openalex_work(
    record: Mapping[str, Any],
    *,
    lineage: CanonicalLineage,
) -> CanonicalWorkBundle:
    """Map one OpenAlex Work object into normalized canonical tables.

    Does not flatten authorship×topic×reference into a single fact table.
    Empty arrays produce zero relationship rows (not a NULL sentinel row).
    """
    if not isinstance(record, Mapping):
        raise MappingError("OpenAlex work record must be an object")

    work_id = normalize_openalex_id(record.get("id"), expected_prefix="W", field_name="id")
    doi = None
    if "doi" in record and record.get("doi") is not None:
        doi = normalize_doi(record.get("doi"))

    open_access = record.get("open_access")
    is_oa = None
    oa_status = None
    if isinstance(open_access, Mapping):
        is_oa = _optional_bool(open_access.get("is_oa"), field_name="open_access.is_oa")
        oa_status = _optional_str(open_access.get("oa_status"))

    primary_location = record.get("primary_location")
    primary_source_id = None
    primary_publisher_id = None
    authors: dict[str, Author] = {}
    institutions: dict[str, Institution] = {}
    sources: dict[str, Source] = {}
    publishers: dict[str, Publisher] = {}
    topics: dict[str, Topic] = {}
    funders: dict[str, Funder] = {}
    work_authors: list[WorkAuthor] = []
    work_author_institutions: list[WorkAuthorInstitution] = []
    work_topics: list[WorkTopic] = []
    work_keywords: list[WorkKeyword] = []
    work_references: list[WorkReference] = []
    work_mesh: list[WorkMesh] = []
    work_locations: list[WorkLocation] = []
    work_grants: list[WorkGrant] = []

    def upsert_source(source_obj: Mapping[str, Any] | None) -> str | None:
        nonlocal primary_publisher_id
        if source_obj is None:
            return None
        if not isinstance(source_obj, Mapping):
            raise MappingError("location.source must be an object")
        source_id = normalize_openalex_id(
            source_obj.get("id"), expected_prefix="S", field_name="source.id"
        )
        host = source_obj.get("host_organization")
        host_id = try_normalize_openalex_id(host, expected_prefix="P")
        if host_id is not None:
            publishers.setdefault(
                host_id,
                Publisher(
                    publisher_id=host_id,
                    publisher_id_url=openalex_url(host_id),
                    display_name=_optional_str(source_obj.get("host_organization_name")),
                    lineage=lineage,
                ),
            )
        issn_l = None
        if source_obj.get("issn_l") is not None:
            issn_l = normalize_issn(source_obj.get("issn_l"), field_name="issn_l")
        sources.setdefault(
            source_id,
            Source(
                source_id=source_id,
                source_id_url=openalex_url(source_id),
                display_name=_optional_str(source_obj.get("display_name")),
                source_type=_optional_str(source_obj.get("type")),
                issn_l=issn_l,
                host_publisher_id=host_id,
                lineage=lineage,
            ),
        )
        return source_id

    if isinstance(primary_location, Mapping):
        primary_source_id = upsert_source(
            primary_location.get("source")
            if isinstance(primary_location.get("source"), (Mapping, type(None)))
            else None
        )
        if primary_source_id is not None:
            primary_publisher_id = sources[primary_source_id].host_publisher_id

    authorships_presence = _presence(record, "authorships")
    if authorships_presence is ArrayPresence.PRESENT:
        authorships = record["authorships"]
        assert isinstance(authorships, list)
        for authorship_index, authorship in enumerate(authorships):
            if not isinstance(authorship, Mapping):
                raise MappingError("authorships entries must be objects")
            author_obj = authorship.get("author")
            author_id = None
            if isinstance(author_obj, Mapping) and author_obj.get("id") is not None:
                author_id = normalize_openalex_id(
                    author_obj.get("id"), expected_prefix="A", field_name="author.id"
                )
                orcid = None
                if author_obj.get("orcid") is not None:
                    orcid = normalize_orcid(author_obj.get("orcid"))
                authors.setdefault(
                    author_id,
                    Author(
                        author_id=author_id,
                        author_id_url=openalex_url(author_id),
                        display_name=_optional_str(author_obj.get("display_name")),
                        orcid=orcid,
                        lineage=lineage,
                    ),
                )
            work_authors.append(
                WorkAuthor(
                    work_id=work_id,
                    authorship_index=authorship_index,
                    author_id=author_id,
                    author_position=_optional_str(authorship.get("author_position")),
                    is_corresponding=_optional_bool(
                        authorship.get("is_corresponding"),
                        field_name="is_corresponding",
                    ),
                    raw_author_name=_optional_str(authorship.get("raw_author_name")),
                    lineage=lineage,
                )
            )
            institutions_value = authorship.get("institutions")
            if institutions_value is None:
                continue
            if not isinstance(institutions_value, list):
                raise MappingError("authorships.institutions must be an array or null")
            for institution_index, institution_obj in enumerate(institutions_value):
                if not isinstance(institution_obj, Mapping):
                    raise MappingError("institutions entries must be objects")
                institution_id = None
                if institution_obj.get("id") is not None:
                    institution_id = normalize_openalex_id(
                        institution_obj.get("id"),
                        expected_prefix="I",
                        field_name="institution.id",
                    )
                    ror = None
                    if institution_obj.get("ror") is not None:
                        ror = normalize_ror(institution_obj.get("ror"))
                    country = _optional_str(institution_obj.get("country_code"))
                    if country is not None:
                        country = country.upper()
                    institutions.setdefault(
                        institution_id,
                        Institution(
                            institution_id=institution_id,
                            institution_id_url=openalex_url(institution_id),
                            display_name=_optional_str(institution_obj.get("display_name")),
                            ror=ror,
                            country_code=country,
                            institution_type=_optional_str(institution_obj.get("type")),
                            lineage=lineage,
                        ),
                    )
                work_author_institutions.append(
                    WorkAuthorInstitution(
                        work_id=work_id,
                        authorship_index=authorship_index,
                        institution_index=institution_index,
                        institution_id=institution_id,
                        lineage=lineage,
                    )
                )

    topics_presence = _presence(record, "topics")
    if topics_presence is ArrayPresence.PRESENT:
        seen_topics: set[str] = set()
        for topic_obj in record["topics"]:
            if not isinstance(topic_obj, Mapping):
                raise MappingError("topics entries must be objects")
            topic_id = normalize_openalex_id(
                topic_obj.get("id"), expected_prefix="T", field_name="topic.id"
            )
            if topic_id in seen_topics:
                continue
            seen_topics.add(topic_id)
            topics.setdefault(
                topic_id,
                Topic(
                    topic_id=topic_id,
                    topic_id_url=openalex_url(topic_id),
                    display_name=_optional_str(topic_obj.get("display_name")),
                    lineage=lineage,
                ),
            )
            work_topics.append(
                WorkTopic(
                    work_id=work_id,
                    topic_id=topic_id,
                    score=_optional_float(topic_obj.get("score"), field_name="topic.score"),
                    lineage=lineage,
                )
            )

    keywords_presence = _presence(record, "keywords")
    if keywords_presence is ArrayPresence.PRESENT:
        seen_keywords: set[str] = set()
        for keyword_obj in record["keywords"]:
            if not isinstance(keyword_obj, Mapping):
                raise MappingError("keywords entries must be objects")
            keyword_id = normalize_keyword_id(keyword_obj.get("id"), field_name="keyword.id")
            if keyword_id in seen_keywords:
                continue
            seen_keywords.add(keyword_id)
            work_keywords.append(
                WorkKeyword(
                    work_id=work_id,
                    keyword_id=keyword_id,
                    display_name=_optional_str(keyword_obj.get("display_name")),
                    score=_optional_float(keyword_obj.get("score"), field_name="keyword.score"),
                    lineage=lineage,
                )
            )

    mesh_presence = _presence(record, "mesh")
    if mesh_presence is ArrayPresence.PRESENT:
        for mesh_index, mesh_obj in enumerate(record["mesh"]):
            if not isinstance(mesh_obj, Mapping):
                raise MappingError("mesh entries must be objects")
            descriptor_ui = _optional_str(mesh_obj.get("descriptor_ui"))
            if descriptor_ui is None:
                raise MappingError("mesh.descriptor_ui is required when mesh is present")
            work_mesh.append(
                WorkMesh(
                    work_id=work_id,
                    mesh_index=mesh_index,
                    descriptor_ui=descriptor_ui,
                    descriptor_name=_optional_str(mesh_obj.get("descriptor_name")),
                    qualifier_ui=_optional_str(mesh_obj.get("qualifier_ui")),
                    qualifier_name=_optional_str(mesh_obj.get("qualifier_name")),
                    is_major_topic=_optional_bool(
                        mesh_obj.get("is_major_topic"), field_name="is_major_topic"
                    ),
                    lineage=lineage,
                )
            )

    referenced_works_presence = _presence(record, "referenced_works")
    if referenced_works_presence is ArrayPresence.PRESENT:
        for reference_index, raw in enumerate(record["referenced_works"]):
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                work_references.append(
                    WorkReference(
                        work_id=work_id,
                        reference_index=reference_index,
                        referenced_work_id=None,
                        raw_reference=None if raw is None else str(raw),
                        reference_status=ReferenceStatus.MISSING,
                        lineage=lineage,
                    )
                )
                continue
            if not isinstance(raw, str):
                raise MappingError("referenced_works entries must be strings")
            try:
                referenced_work_id = normalize_openalex_id(
                    raw, expected_prefix="W", field_name="referenced_works[]"
                )
                status = ReferenceStatus.RESOLVED_ID
            except IdentifierError:
                referenced_work_id = None
                status = ReferenceStatus.MALFORMED
            work_references.append(
                WorkReference(
                    work_id=work_id,
                    reference_index=reference_index,
                    referenced_work_id=referenced_work_id,
                    raw_reference=raw,
                    reference_status=status,
                    lineage=lineage,
                )
            )

    # Presence describes only the source `locations` field observation.
    locations_presence = _presence(record, "locations")
    location_entries: list[tuple[Mapping[str, object], LocationOrigin]] = []
    if locations_presence is ArrayPresence.PRESENT:
        for location_obj in record["locations"]:
            if not isinstance(location_obj, Mapping):
                raise MappingError("locations entries must be objects")
            location_entries.append((location_obj, LocationOrigin.LOCATIONS_ARRAY))
    elif isinstance(primary_location, Mapping):
        # Fallback creates a WorkLocation without rewriting locations_presence.
        location_entries.append(
            (primary_location, LocationOrigin.PRIMARY_LOCATION_FALLBACK)
        )

    primary_landing = (
        _optional_str(primary_location.get("landing_page_url"))
        if isinstance(primary_location, Mapping)
        else None
    )
    primary_marked = False
    for location_index, (location_obj, location_origin) in enumerate(location_entries):
        source_id = None
        source_obj = location_obj.get("source")
        if isinstance(source_obj, Mapping):
            source_id = upsert_source(source_obj)
        is_primary = False
        if location_origin is LocationOrigin.PRIMARY_LOCATION_FALLBACK:
            is_primary = True
        elif not primary_marked and isinstance(primary_location, Mapping):
            if (
                source_id == primary_source_id
                and primary_landing is not None
                and _optional_str(location_obj.get("landing_page_url")) == primary_landing
            ):
                is_primary = True
            elif (
                source_id == primary_source_id
                and primary_landing is None
                and len(location_entries) == 1
            ):
                is_primary = True
        if is_primary:
            primary_marked = True
        work_locations.append(
            WorkLocation(
                work_id=work_id,
                location_index=location_index,
                location_origin=location_origin,
                source_id=source_id,
                is_oa=_optional_bool(location_obj.get("is_oa"), field_name="location.is_oa"),
                landing_page_url=_optional_str(location_obj.get("landing_page_url")),
                pdf_url=_optional_str(location_obj.get("pdf_url")),
                license=_optional_str(location_obj.get("license")),
                version=_optional_str(location_obj.get("version")),
                is_primary=is_primary,
                lineage=lineage,
            )
        )

    grants_presence = _presence(record, "grants")
    if grants_presence is ArrayPresence.PRESENT:
        for grant_index, grant_obj in enumerate(record["grants"]):
            if not isinstance(grant_obj, Mapping):
                raise MappingError("grants entries must be objects")
            funder_id = None
            if grant_obj.get("funder") is not None:
                funder_id = normalize_openalex_id(
                    grant_obj.get("funder"), expected_prefix="F", field_name="grant.funder"
                )
                funders.setdefault(
                    funder_id,
                    Funder(
                        funder_id=funder_id,
                        funder_id_url=openalex_url(funder_id),
                        display_name=_optional_str(grant_obj.get("funder_display_name")),
                        lineage=lineage,
                    ),
                )
            work_grants.append(
                WorkGrant(
                    work_id=work_id,
                    grant_index=grant_index,
                    funder_id=funder_id,
                    award_id=_optional_str(grant_obj.get("award_id")),
                    funder_display_name=_optional_str(grant_obj.get("funder_display_name")),
                    lineage=lineage,
                )
            )

    title = _optional_str(record.get("title"))
    if title is None:
        title = _optional_str(record.get("display_name"))

    work = Work(
        work_id=work_id,
        work_id_url=openalex_url(work_id),
        doi=doi,
        title=title,
        publication_year=_optional_int(
            record.get("publication_year"), field_name="publication_year"
        ),
        publication_date=_parse_date(record.get("publication_date"), field_name="publication_date"),
        work_type=_optional_str(record.get("type")),
        language=_optional_str(record.get("language")),
        cited_by_count=_optional_int(record.get("cited_by_count"), field_name="cited_by_count"),
        referenced_works_count=_optional_int(
            record.get("referenced_works_count"), field_name="referenced_works_count"
        ),
        is_oa=is_oa,
        oa_status=oa_status,
        primary_source_id=primary_source_id,
        primary_publisher_id=primary_publisher_id,
        source_created_date=_parse_date(record.get("created_date"), field_name="created_date"),
        source_updated_date=_parse_date(record.get("updated_date"), field_name="updated_date"),
        authorships_presence=authorships_presence,
        topics_presence=topics_presence,
        keywords_presence=keywords_presence,
        mesh_presence=mesh_presence,
        referenced_works_presence=referenced_works_presence,
        locations_presence=locations_presence,
        grants_presence=grants_presence,
        lineage=lineage,
    )

    return CanonicalWorkBundle(
        work=work,
        authors=tuple(sorted(authors.values(), key=lambda row: row.author_id)),
        institutions=tuple(
            sorted(institutions.values(), key=lambda row: row.institution_id)
        ),
        sources=tuple(sorted(sources.values(), key=lambda row: row.source_id)),
        publishers=tuple(sorted(publishers.values(), key=lambda row: row.publisher_id)),
        topics=tuple(sorted(topics.values(), key=lambda row: row.topic_id)),
        funders=tuple(sorted(funders.values(), key=lambda row: row.funder_id)),
        work_authors=tuple(work_authors),
        work_author_institutions=tuple(work_author_institutions),
        work_topics=tuple(work_topics),
        work_keywords=tuple(work_keywords),
        work_references=tuple(work_references),
        work_mesh=tuple(work_mesh),
        work_locations=tuple(work_locations),
        work_grants=tuple(work_grants),
    )
