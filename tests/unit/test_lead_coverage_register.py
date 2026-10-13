"""Guard the lead-architecture coverage register (#122, DE-01).

``docs/requirements/openalex-lead-coverage.md`` maps every atomic requirement
of the lead's 30-section architecture to issues, merged PRs, files, tests,
evidence and follow-on issues. These tests keep it mechanically honest: every
section is covered, vocabularies are closed, cited files and test functions
exist, follow-on issue numbers match their DE planning IDs, AWS examples get
no implementation ticket, and no row claims enterprise or production evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTER = REPO_ROOT / "docs/requirements/openalex-lead-coverage.md"
MATRICES = REGISTER.with_name("openalex-lead-coverage-matrices.md")
REPO_URL = "https://github.com/agilandeenadhayalan41/research-intelligence-data-platform"

CLASSES = frozenset({"MANDATORY", "CONDITIONAL", "ILLUSTRATIVE"})
STATUSES = frozenset({"MET", "PARTIAL", "NOT_MET", "DEFERRED", "OUT_OF_SCOPE"})
EVIDENCE = frozenset(
    {
        "DEFINED",
        "SEMANTIC_ONLY",
        "CLOUD_SYNTHETIC_MEASURED",
        "PERSONAL_DEV_QA_VERIFIED",
        "MISSING",
        "CONDITIONAL_DEFERRED",
        "NOT_APPLICABLE",
    }
)
# No enterprise-managed or production evidence exists yet. A row may only use
# these labels once that evidence is recorded; move the label into EVIDENCE in
# the same PR that links the evidence.
UNPROVEN_EVIDENCE = frozenset(
    {"ENTERPRISE_DEV_VERIFIED", "ENTERPRISE_QA_VERIFIED", "PROD_DEPLOYED"}
)
OWNERS = frozenset(
    {
        "Data Engineering",
        "Lead architect",
        "Cloud platform",
        "Security and governance",
        "API product owner",
        "Downstream owners (post-G5)",
    }
)
# DE-01..DE-35 were created as issues #122..#156 (DE-k is #121+k).
DE_ISSUE_OFFSET = 121
DE_COUNT = 35
FIRST_DE_ISSUE = DE_ISSUE_OFFSET + 1

ALL_SECTIONS = tuple(str(n) for n in range(1, 31))
SUBSECTIONS = ("3.1", "6.1", "6.2", "8.1", "8.2", "8.3", "8.4", "10.1")

COLUMNS = (
    "ID",
    "Sec",
    "Requirement (lead wording)",
    "Class",
    "Prior issues / merged PRs",
    "Paths",
    "Tests / evidence",
    "Evidence",
    "Status",
    "Gap",
    "Follow-on",
    "Owner",
)

ID_RE = re.compile(r"^L(\d{2})(?:\.(\d))?-(\d{2})$")
SHA_RE = re.compile(r"`main` @ `([0-9a-f]{40})`")
REF_DEF_RE = re.compile(r"^\[#(\d+)\]: (\S+)$", re.MULTILINE)
REF_USE_RE = re.compile(r"\[#(\d+)\](?!:)")
FOLLOW_ON_ITEM_RE = re.compile(r"^DE-(\d{2}) \[#(\d+)\]$")
BACKTICK_RE = re.compile(r"`([^`]+)`")
MD_LINK_RE = re.compile(r"\]\((?!https?://|#)([^)\s]+)\)")
EMPTY = "—"


@dataclass(frozen=True)
class Row:
    line: int
    cells: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.cells[0]

    @property
    def section(self) -> str:
        return self.cells[1]

    @property
    def requirement(self) -> str:
        return self.cells[2]


def _split_cells(line: str) -> tuple[str, ...]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return tuple(cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body))


def _section_body(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    if match is None:
        return ""
    rest = text[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    return rest if following is None else rest[: following.start()]


def parse_rows(text: str) -> list[Row]:
    body = _section_body(text, "Register")
    offset = text[: text.find(body)].count("\n") if body else 0
    rows = []
    for index, line in enumerate(body.splitlines()):
        if line.startswith("| L"):
            rows.append(Row(line=offset + index + 1, cells=_split_cells(line)))
    return rows


def _items(cell: str) -> list[str]:
    return [] if cell == EMPTY else BACKTICK_RE.findall(cell)


def _refs(cell: str, label: str) -> list[int]:
    match = re.search(rf"{label} ((?:\[#\d+\] ?)+)", cell)
    return [] if match is None else [int(n) for n in REF_USE_RE.findall(match.group(1))]


def _follow_on(cell: str) -> tuple[list[tuple[int, int]], list[str]]:
    if cell == EMPTY:
        return [], []
    pairs, errors = [], []
    for item in cell.split("; "):
        match = FOLLOW_ON_ITEM_RE.match(item)
        if match is None:
            errors.append(f"malformed follow-on item {item!r}")
            continue
        pairs.append((int(match.group(1)), int(match.group(2))))
    return pairs, errors


def _node_errors(node: str, repo_root: Path) -> list[str]:
    path_part, *names = node.split("::")
    path = repo_root / path_part
    if not path.is_file():
        return [f"test file {path_part} does not exist"]
    source = path.read_text(encoding="utf-8")
    errors = []
    for name in names:
        bare = name.split("[", 1)[0]
        if not re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(bare)}\b", source, re.MULTILINE):
            errors.append(f"{bare} is not defined in {path_part}")
    return errors


def _document_errors(text: str, repo_root: Path) -> tuple[list[str], dict[int, str]]:
    """Checks shared by the register and its matrices page."""
    errors: list[str] = []
    if SHA_RE.search(text) is None:
        errors.append("header must record the inspected `main` @ `<40-hex SHA>`")

    definitions: dict[int, str] = {}
    for number, url in REF_DEF_RE.findall(text):
        expected = {f"{REPO_URL}/issues/{number}", f"{REPO_URL}/pull/{number}"}
        if url not in expected:
            errors.append(f"reference [#{number}] points at {url}")
        definitions[int(number)] = url
    for number in sorted({int(n) for n in REF_USE_RE.findall(text)}):
        if number not in definitions:
            errors.append(f"[#{number}] is used but has no link definition")

    register_dir = repo_root / REGISTER.parent.relative_to(REPO_ROOT)
    for target in MD_LINK_RE.findall(text):
        relative = target.split("#", 1)[0]
        if relative and not (register_dir / relative).resolve().exists():
            errors.append(f"relative link {target} does not resolve")
    for span in set(BACKTICK_RE.findall(text)):
        # Code spans that look like repository paths must exist; field names
        # such as `works.doi` or `authorships[].author` have no slash.
        if "/" in span and not re.search(r"[\s\[{<]|^https?:", span):
            path = span.split("#", 1)[0].split("::", 1)[0]
            if not (repo_root / path).exists():
                errors.append(f"path {span} does not exist")
    return errors, definitions


def validate_matrices(text: str, repo_root: Path) -> list[str]:
    """Return every rule violation in the matrices companion page."""
    errors, _ = _document_errors(text, repo_root)
    for label in sorted(UNPROVEN_EVIDENCE):
        if label in text:
            errors.append(f"{label} claimed but no such evidence is recorded")
    if not re.search(r"^\| ", text, re.MULTILINE):
        errors.append("matrices page has no tables")
    return errors


def validate_register(
    text: str,
    repo_root: Path,
    *,
    sections: tuple[str, ...] = ALL_SECTIONS,
    subsections: tuple[str, ...] = SUBSECTIONS,
    anchors: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Return every rule violation in a register document (empty when valid)."""
    errors, definitions = _document_errors(text, repo_root)

    for heading in sections:
        if re.search(rf"^### §{heading} \S", text, re.MULTILINE) is None:
            errors.append(f"missing register heading for §{heading}")

    rows = parse_rows(text)
    if not rows:
        errors.append("register has no rows")
    seen_ids: set[str] = set()
    sequence: dict[str, int] = {}
    covered: set[str] = set()
    for row in rows:
        where = f"line {row.line} ({row.id})"
        if len(row.cells) != len(COLUMNS):
            errors.append(f"{where}: expected {len(COLUMNS)} cells, found {len(row.cells)}")
            continue
        (
            row_id,
            section,
            requirement,
            cls,
            refs,
            paths,
            tests,
            evidence,
            status,
            gap,
            follow_on,
            owner,
        ) = row.cells
        match = ID_RE.match(row_id)
        if match is None:
            errors.append(f"{where}: malformed ID")
        else:
            major, minor, seq = match.groups()
            id_section = str(int(major)) + (f".{minor}" if minor else "")
            if id_section != section:
                errors.append(f"{where}: ID section {id_section} != Sec {section}")
            expected_seq = sequence.get(section, 0) + 1
            if int(seq) != expected_seq:
                errors.append(f"{where}: expected sequence {expected_seq:02d}")
            sequence[section] = int(seq)
        if row_id in seen_ids:
            errors.append(f"{where}: duplicate ID")
        seen_ids.add(row_id)
        covered.add(section)
        if not requirement or requirement == EMPTY:
            errors.append(f"{where}: empty requirement")
        if cls not in CLASSES:
            errors.append(f"{where}: unknown class {cls!r}")
        if evidence in UNPROVEN_EVIDENCE:
            errors.append(f"{where}: {evidence} claimed but no such evidence is recorded")
        elif evidence not in EVIDENCE:
            errors.append(f"{where}: unknown evidence {evidence!r}")
        if status not in STATUSES:
            errors.append(f"{where}: unknown status {status!r}")
        if owner not in OWNERS:
            errors.append(f"{where}: unknown owner {owner!r}")
        if not gap:
            errors.append(f"{where}: empty gap (use 'None' when met)")

        for number in _refs(refs, "Issues"):
            if number >= FIRST_DE_ISSUE:
                errors.append(f"{where}: #{number} is not a prior issue (use Follow-on)")
            if definitions.get(number, "").find("/issues/") < 0:
                errors.append(f"{where}: issue #{number} must link to /issues/")
        for number in _refs(refs, "PRs"):
            if definitions.get(number, "").find("/pull/") < 0:
                errors.append(f"{where}: PR #{number} must link to /pull/")
        if refs != EMPTY and not re.fullmatch(
            r"(?:Issues (?:\[#\d+\] ?)+)?(?:; )?(?:PRs (?:\[#\d+\] ?)+)?", refs
        ):
            errors.append(f"{where}: malformed issue/PR cell {refs!r}")

        for path in _items(paths):
            if not (repo_root / path).exists():
                errors.append(f"{where}: path {path} does not exist")
        for item in _items(tests):
            if "::" in item:
                errors.extend(f"{where}: {e}" for e in _node_errors(item, repo_root))
            elif not (repo_root / item.split("#", 1)[0]).exists():
                errors.append(f"{where}: evidence path {item} does not exist")

        pairs, follow_errors = _follow_on(follow_on)
        errors.extend(f"{where}: {e}" for e in follow_errors)
        for de, number in pairs:
            if not 1 <= de <= DE_COUNT:
                errors.append(f"{where}: DE-{de:02d} is outside DE-01..DE-{DE_COUNT}")
            elif number != DE_ISSUE_OFFSET + de:
                errors.append(f"{where}: DE-{de:02d} is #{DE_ISSUE_OFFSET + de}, not #{number}")

        if status in {"MET", "OUT_OF_SCOPE"} and pairs:
            errors.append(f"{where}: {status} rows take no follow-on")
        if status in {"PARTIAL", "NOT_MET"} and not pairs:
            errors.append(f"{where}: {status} rows need a follow-on issue")
        if status == "MET" and evidence == "MISSING":
            errors.append(f"{where}: MET with MISSING evidence")
        if evidence == "MISSING" and status not in {"NOT_MET", "DEFERRED"}:
            errors.append(f"{where}: MISSING evidence must be NOT_MET or DEFERRED")
        if status == "DEFERRED" and evidence not in {"CONDITIONAL_DEFERRED", "MISSING"}:
            errors.append(f"{where}: DEFERRED rows use CONDITIONAL_DEFERRED evidence")
        if (status == "OUT_OF_SCOPE") != (evidence == "NOT_APPLICABLE"):
            errors.append(f"{where}: OUT_OF_SCOPE and NOT_APPLICABLE go together")
        if requirement.startswith("AWS example:") and not (
            cls == "ILLUSTRATIVE" and status == "OUT_OF_SCOPE" and not pairs
        ):
            errors.append(f"{where}: AWS examples are ILLUSTRATIVE, OUT_OF_SCOPE, no follow-on")

    covered_majors = {section.split(".", 1)[0] for section in covered}
    for section in sections:
        if section not in covered_majors:
            errors.append(f"§{section} has no register rows")
    for section in subsections:
        if section not in covered:
            errors.append(f"§{section} has no register rows")

    for section, terms in (anchors or {}).items():
        texts = [row.requirement.casefold() for row in rows if row.section == section]
        for term in terms:
            if not any(term.casefold() in requirement for requirement in texts):
                errors.append(f"§{section}: lead item {term!r} is not mapped to a row")
    return errors


# Lead items that must each be traceable to a row of the named section. They
# are the lead's own words for entities, fields, queries, endpoints, technology
# candidates and benchmark items, so dropping one fails CI.
LEAD_ANCHORS: dict[str, tuple[str, ...]] = {
    "1": (
        "Count authors associated with a journal",
        "Count articles belonging to a publisher",
        "degree/order of edges",
        "KG construction",
        "by DOI",
        "eISSN or ISSN",
        "by OpenAlex ID",
        "institution",
        "same physical storage",
        "common API layer",
    ),
    "2": (
        "logical capabilities",
        "Object storage",
        "Columnar format",
        "Serverless analytical query",
        "Managed analytical warehouse",
        "Distributed SQL over lake",
        "Batch/data processing",
        "Operational relational store",
        "API/runtime",
        "Catalog/governance",
        "AWS example: S3",
        "Athena",
        "Redshift",
        "Trino/EMR",
        "Glue",
        "RDS PostgreSQL",
        "ECS/EKS/Lambda",
        "Lake Formation",
        "Cloud Storage",
        "BigLake",
        "Trino/Dataproc",
        "Dataflow",
        "Cloud SQL PostgreSQL",
        "AlloyDB",
        "Cloud Run",
        "Dataplex",
        "Data Catalog",
        "not architectural requirements",
    ),
    "3.1": (
        "retained in object storage using Parquet",
        "analytical queries",
        "data science",
        "enrichment",
        "entity resolution",
        "KG generation",
        "reprocessing",
        "rebuilding downstream serving stores",
        "recovering from schema or pipeline changes",
        "should not depend on PostgreSQL",
        "Acquire once",
        "Store durably",
        "multiple engines",
    ),
    "4": (
        "Works",
        "Authors",
        "Sources / Journals",
        "Publishers",
        "Institutions",
        "Topics",
        "Funders",
        "Locations",
        "Authorships",
        "Work ↔ Topic",
        "Work ↔ Institution",
        "Work ↔ Work",
        "Full nested metadata",
        "Historical/raw source fields",
        "Work ↔ Author",
        "should not automatically be copied",
    ),
    "5": (
        "JOIN",
        "FILTER",
        "GROUP BY",
        "COUNT DISTINCT",
        "large scans",
        "aggregations",
        "relationship analysis",
        "BigQuery external tables",
        "BigLake",
        "Spark / Dataproc",
        "DuckDB",
        "shared production query platform",
    ),
    "6.1": ("COUNT(DISTINCT wa.author_id)", "deduplicate authors"),
    "6.2": (
        "COUNT(DISTINCT w.work_id)",
        "Author → Work → Journal",
        "Institution → Author → Work",
        "Publisher → Journal → Work",
        "Work → Topic",
        "Work → Work citation",
        "Publisher × Topic × License",
        "Journal × Author",
        "Institution × Topic",
        "OLAP",
    ),
    "7": ("low-latency access", "should not depend on PostgreSQL", "Aurora", "Cloud SQL", "AlloyDB"),
    "8.1": (
        "openalex_id",
        "doi",
        "title",
        "publication_date",
        "publication_year",
        "work_type",
        "language",
        "source_id",
        "publisher_id",
        "license",
        "is_oa",
        "oa_status",
        "primary_topic_id",
        "cited_by_count",
        "updated_date",
        "indexes",
    ),
    "8.2": (
        "source_id",
        "display_name",
        "issn",
        "eissn",
        "publisher_id",
        "source_type",
        "is_oa",
        "homepage_url",
        "updated_date",
        "eISSN → source",
        "ISSN → source",
        "OpenAlex source ID → source",
    ),
    "8.3": ("publisher_id", "display_name", "parent_publisher_id", "updated_date"),
    "8.4": (
        "ORCID → Author",
        "Author ID → profile",
        "ROR → Institution",
        "Institution ID → profile",
        "only because it exists in OpenAlex",
    ),
    "9": (
        "Work ↔ Author relationship set",
        "Work ↔ Topic relationship set",
        "Work ↔ Institution relationship set",
        "citation/reference graph",
        "location history",
        "nested authorship",
        "nested source/location",
        "historical/raw representations",
        "offline processing",
        "analytical warehouse",
    ),
    "10": ("workload behavior",),
    "10.1": (
        "Get metadata for DOI X",
        "Get journal for eISSN X",
        "Get source by OpenAlex ID",
        "Get publisher metadata",
        "small result set",
        "high selectivity",
        "low latency",
        "high concurrency",
        "frequently repeated",
        "Operational serving store",
    ),
    "11": (
        "unique authors published in Journal X",
        "Publisher Y",
        "institutions contribute the most works",
        "degree distribution",
        "large joins",
        "large relationship sets",
        "COUNT DISTINCT",
        "GROUP BY",
        "scans",
        "dynamic filters",
        "Analytical query layer",
    ),
    "12": (
        "journal_id",
        "article_count",
        "unique_author_count",
        "citation_count",
        "first_publication_year",
        "latest_publication_year",
        "updated_at",
        "publisher_id",
        "topic_id",
        "license",
        "publication_year",
        "Redis",
        "repeated usage justifies",
    ),
    "13": (
        "GET /works/{doi}",
        "GET /sources/{eissn}",
        "GET /publishers/{publisherId}",
        "GET /journals/{journalId}/metrics",
        "article-count",
        "POST /analytics/query",
        "domain-specific analytical APIs",
    ),
    "14": (
        "Hide storage implementation",
        "expected latency",
        "freshness",
        "supported filters",
        "pagination",
        "limits",
        "consistency expectations",
        "synchronous",
        "asynchronous",
    ),
    "15": (
        "route requests based on query characteristics",
        "DOI lookup",
        "eISSN lookup",
        "Known journal author count",
        "Arbitrary publisher/topic/license",
        "transparent to consumers",
    ),
    "16": (
        "POST /sql",
        "physical schemas",
        "table names",
        "join logic",
        "storage engine assumptions",
        "schema evolution",
        "implementation details",
        "debugging",
    ),
    "17": ("Enrichment", "KG Generation", "Serving Projection", "Cloud SQL", "AlloyDB"),
    "18": (
        "new records",
        "changed records",
        "deleted records",
        "schema changes",
        "reprocessing",
        "avoid rebuilding",
    ),
    "19": (
        "separate lake query engine",
        "work_authors",
        "work_topics",
        "work_institutions",
        "citations",
        "test this rather than assume",
    ),
    "20": (
        "identifier → entity resolution",
        "DOI → metadata",
        "eISSN → source",
        "OpenAlex ID → entity",
        "tens or hundreds of GB",
        "real prototype",
    ),
    "21": (
        "WHERE doi = @doi",
        "latency",
        "concurrency behavior",
        "cost",
        "API SLA",
        "caching",
        "small operational store",
    ),
    "22": ("API/cache", "Cloud SQL PostgreSQL", "Athena/Trino", "measured needs"),
    "23": (
        "Identifier / Metadata Lookup",
        "Known Aggregate",
        "Dynamic Analytical Query",
        "journal article count",
        "publisher topic distribution",
        "precompute/materialize",
        "seconds may be acceptable",
    ),
    "24": (
        "durability",
        "open formats",
        "portability",
        "lifecycle management",
        "schema evolution",
        "large join performance",
        "data scanned",
        "partition pruning",
        "clustering",
        "materialization capability",
        "operational overhead",
        "security/governance",
        "point lookup latency",
        "index support",
        "data size",
        "update behavior",
        "availability requirements",
    ),
    "25": (
        "DOI → work metadata",
        "eISSN → source metadata",
        "OpenAlex work ID → metadata",
        "publisher ID → metadata",
        "unique authors for one journal",
        "unique authors across all journals for a publisher",
        "article count by publisher + topic",
        "topic distribution within a publisher",
        "author → article → journal",
        "institution → author → article",
        "data scanned",
        "query cost",
        "CPU/memory",
        "storage size",
        "ingestion/update duration",
        "operational complexity",
    ),
    "26": (
        "small number of records",
        "queried frequently",
        "efficiently indexed",
        "large portions of the dataset",
        "many-to-many",
        "filters are dynamic",
        "exploratory or batch-oriented",
        "repeatedly requested",
    ),
    "27": (
        "open table formats",
        "portable schemas",
        "service APIs",
        "logical data models",
        "BigQuery-specific SQL",
        "PostgreSQL schemas",
        "cloud-specific storage paths",
        "BigQuery clustering",
        "partition projection",
        "Redshift sort keys",
        "BigLake metadata",
        "PostgreSQL indexes",
    ),
    "28": (
        "time travel",
        "ACID writes",
        "multiple analytical engines",
        "Apache Iceberg",
        "first iteration",
    ),
    "29": (
        "Canonical Data Layer",
        "Analytical Query Layer",
        "Data API",
        "only where measured requirements justify it",
        "DOI/eISSN lookup latency",
    ),
    "30": (
        "canonical data layer",
        "Analytical engines handle large joins",
        "optional serving projections",
        "stable data APIs",
    ),
}


@pytest.fixture(scope="module")
def register_text() -> str:
    return REGISTER.read_text(encoding="utf-8")


def test_register_is_valid(register_text: str) -> None:
    assert validate_register(register_text, REPO_ROOT, anchors=LEAD_ANCHORS) == []


def test_register_has_the_documented_columns(register_text: str) -> None:
    body = _section_body(register_text, "Register")
    headers = [_split_cells(line) for line in body.splitlines() if line.startswith("| ID ")]
    assert headers, "register tables need a header row"
    assert set(headers) == {COLUMNS}


def test_register_records_special_decisions(register_text: str) -> None:
    body = _section_body(register_text, "Special decisions")
    for number in (23, 108, 118):
        assert f"[#{number}]" in body


def test_matrices_page_is_valid_and_linked(register_text: str) -> None:
    assert validate_matrices(MATRICES.read_text(encoding="utf-8"), REPO_ROOT) == []
    assert f"]({MATRICES.name})" in register_text


def test_register_is_linked_from_architecture_and_site() -> None:
    architecture = (REPO_ROOT / "docs/architecture/platform-architecture.md").read_text(encoding="utf-8")
    assert "../requirements/openalex-lead-coverage.md" in architecture
    sidebars = (REPO_ROOT / "docs/site/sidebars.ts").read_text(encoding="utf-8")
    assert sidebars.count("'requirements/openalex-lead-coverage'") == 1
    assert sidebars.count("'requirements/openalex-lead-coverage-matrices'") == 1


# --- Negative cases: the validator itself must reject bad registers. ---

_HEADER = "| " + " | ".join(COLUMNS) + " |\n|" + "---|" * len(COLUMNS) + "\n"


def _register(*rows: str, sections: tuple[str, ...] = ("1",), extra: str = "") -> str:
    headings = "".join(f"### §{s} Title\n\n" for s in sections)
    return (
        "# Register\n\n`main` @ `" + "a" * 40 + "`\n\n## Register\n\n"
        + headings
        + _HEADER
        + "".join(f"{row}\n" for row in rows)
        + "\n"
        + extra
        + f"[#122]: {REPO_URL}/issues/122\n[#135]: {REPO_URL}/issues/135\n"
        + f"[#15]: {REPO_URL}/issues/15\n[#48]: {REPO_URL}/pull/48\n"
    )


def _row(**overrides: str) -> str:
    cells = {
        "id": "L01-01",
        "sec": "1",
        "req": "Retrieve article metadata by DOI.",
        "cls": "MANDATORY",
        "refs": "Issues [#15]; PRs [#48]",
        "paths": "`tests/unit/test_example.py`",
        "tests": "`tests/unit/test_example.py::test_present`",
        "evidence": "DEFINED",
        "status": "PARTIAL",
        "gap": "No HTTP endpoint.",
        "follow": "DE-14 [#135]",
        "owner": "Data Engineering",
    }
    cells.update(overrides)
    return "| " + " | ".join(cells.values()) + " |"


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    tests_dir = tmp_path / "tests/unit"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_example.py").write_text("def test_present():\n    pass\n", encoding="utf-8")
    (tmp_path / "docs/requirements").mkdir(parents=True)
    return tmp_path


def _errors(text: str, repo: Path, **kwargs: object) -> list[str]:
    return validate_register(text, repo, sections=("1",), subsections=(), **kwargs)  # type: ignore[arg-type]


def test_validator_accepts_a_minimal_valid_register(fake_repo: Path) -> None:
    assert _errors(_register(_row()), fake_repo) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"evidence": "PROD_DEPLOYED"}, "PROD_DEPLOYED claimed but no such evidence is recorded"),
        ({"evidence": "ENTERPRISE_QA_VERIFIED"}, "ENTERPRISE_QA_VERIFIED claimed"),
        ({"evidence": "WORKS_ON_MY_LAPTOP"}, "unknown evidence"),
        ({"status": "DONE"}, "unknown status"),
        ({"cls": "NICE_TO_HAVE"}, "unknown class"),
        ({"owner": "Someone"}, "unknown owner"),
        ({"paths": "`src/missing.py`"}, "path src/missing.py does not exist"),
        ({"tests": "`tests/unit/test_example.py::test_absent`"}, "test_absent is not defined"),
        ({"tests": "`tests/unit/test_missing.py::test_present`"}, "test file tests/unit/test_missing.py"),
        ({"follow": "DE-14 [#122]"}, "DE-14 is #135, not #122"),
        ({"follow": "DE-99 [#122]"}, "outside DE-01..DE-35"),
        ({"follow": "#135"}, "malformed follow-on item"),
        ({"follow": EMPTY}, "PARTIAL rows need a follow-on issue"),
        ({"status": "MET"}, "MET rows take no follow-on"),
        ({"evidence": "MISSING", "status": "MET", "follow": EMPTY}, "MET with MISSING evidence"),
        ({"refs": "Issues [#122]"}, "#122 is not a prior issue"),
        ({"refs": "Issues [#48]"}, "issue #48 must link to /issues/"),
        ({"id": "L02-01"}, "ID section 2 != Sec 1"),
        ({"id": "L01-02"}, "expected sequence 01"),
        ({"gap": ""}, "empty gap"),
        (
            {"req": "AWS example: S3", "cls": "ILLUSTRATIVE"},
            "AWS examples are ILLUSTRATIVE, OUT_OF_SCOPE, no follow-on",
        ),
        (
            {"status": "OUT_OF_SCOPE", "follow": EMPTY},
            "OUT_OF_SCOPE and NOT_APPLICABLE go together",
        ),
    ],
)
def test_validator_rejects_rule_violations(
    fake_repo: Path, overrides: dict[str, str], expected: str
) -> None:
    errors = _errors(_register(_row(**overrides)), fake_repo)
    assert any(expected in error for error in errors), errors


def test_validator_rejects_missing_sections_and_anchors(fake_repo: Path) -> None:
    text = _register(_row())
    errors = validate_register(
        text, fake_repo, sections=("1", "2"), subsections=("8.1",), anchors={"1": ("eISSN",)}
    )
    assert "missing register heading for §2" in errors
    assert "§2 has no register rows" in errors
    assert "§8.1 has no register rows" in errors
    assert "§1: lead item 'eISSN' is not mapped to a row" in errors


def test_validator_rejects_duplicate_ids_and_undefined_links(fake_repo: Path) -> None:
    errors = _errors(_register(_row(), _row(id="L01-01", refs="Issues [#7]")), fake_repo)
    assert any("duplicate ID" in error for error in errors)
    assert "[#7] is used but has no link definition" in errors


def test_validator_rejects_a_missing_sha_and_broken_relative_links(fake_repo: Path) -> None:
    text = _register(_row(), extra="See [roadmap](../architecture/missing.md).\n\n")
    errors = _errors(text.replace("`main` @ `" + "a" * 40 + "`", "main"), fake_repo)
    assert "header must record the inspected `main` @ `<40-hex SHA>`" in errors
    assert "relative link ../architecture/missing.md does not resolve" in errors


def test_validator_accepts_aws_examples_without_tickets(fake_repo: Path) -> None:
    aws = _row(
        req="AWS example: S3",
        cls="ILLUSTRATIVE",
        refs=EMPTY,
        paths=EMPTY,
        tests=EMPTY,
        evidence="NOT_APPLICABLE",
        status="OUT_OF_SCOPE",
        gap="Portability reference only.",
        follow=EMPTY,
        owner="Lead architect",
    )
    assert _errors(_register(aws), fake_repo) == []


def test_matrices_validator_rejects_bad_pages(fake_repo: Path) -> None:
    page = (
        "# Matrices\n\n`main` @ `" + "a" * 40 + "`\n\n"
        "| Item | Where | Evidence |\n|---|---|---|\n"
        "| DOI | `src/missing.py` | PROD_DEPLOYED [#9] |\n"
    )
    errors = validate_matrices(page, fake_repo)
    assert "path src/missing.py does not exist" in errors
    assert "PROD_DEPLOYED claimed but no such evidence is recorded" in errors
    assert "[#9] is used but has no link definition" in errors
    good = (
        "# Matrices\n\n`main` @ `" + "a" * 40 + "`\n\n"
        "| Item | Where |\n|---|---|\n"
        "| `works.doi` | `tests/unit/test_example.py::test_present` [#15] |\n\n"
        f"[#15]: {REPO_URL}/issues/15\n"
    )
    assert validate_matrices(good, fake_repo) == []
