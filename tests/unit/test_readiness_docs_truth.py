"""Keep readiness docs and placeholder configs in step with the registry (#123, DE-02).

``research_platform.readiness`` is the single source of readiness truth. Every
expectation below is derived from it, so the checks survive unrelated doc edits
and fail when a doc promotes an environment or a domain:

- Every readiness matrix (a Markdown table with ``GCP_SANDBOX`` and ``PROD``
  columns) in the checked docs carries the registry's level in each
  (environment, domain) cell and the registry's overall status in its
  ``Overall`` row. The matrix in ``environment-readiness.md`` must also cover
  every registry domain exactly once.
- Prose and non-matrix table cells never give an environment an overall status
  or a level the registry does not assign: no ``READY_TO_VALIDATE`` / ``READY``
  / ``VERIFIED`` for an environment overall while it is ``NOT_READY``, nothing
  for PROD beyond what the registry gives it (today ``NOT_READY`` overall and
  ``BLOCKED`` for every mandatory domain), and no "<level> for <env> only"
  unless the registry agrees. Each checked doc must also state that every
  ``NOT_READY`` cloud environment is ``NOT_READY``. Sections that quote stale
  statements on purpose (for example "Known inconsistencies in the docs") are
  skipped.
- ``config/dev.yaml``, ``qa.yaml`` and ``prod.yaml`` keep the local-only
  placeholder disclaimer that the registry's CONFIGURATION evidence relies on.

Negative cases feed promoted registries, stale sentences from earlier versions
of the docs and broken configs to the same checkers.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from research_platform.readiness import (
    CLOUD_PROMOTION_ORDER,
    DOMAIN_INVENTORY,
    MANDATORY_CLOUD_DOMAINS,
    EnvironmentName,
    EnvironmentOverallStatus,
    EvidenceLabel,
    EvidenceLevel,
    ReadinessDomain,
    ReadinessEntry,
    build_readiness_registry,
    can_promote,
    evaluate_environment_readiness,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
READINESS_DOC = "docs/architecture/environment-readiness.md"
STATUS_DOCS = (
    "README.md",
    "docs/architecture/roadmap.md",
    "docs/overview/status.md",
    READINESS_DOC,
    "docs/operations/enterprise-environment-inventory.md",
)
PLACEHOLDER_CONFIGS = {
    EnvironmentName.DEV: "config/dev.yaml",
    EnvironmentName.QA: "config/qa.yaml",
    EnvironmentName.PROD: "config/prod.yaml",
}


def _alternation(names: Iterable[str]) -> str:
    # Longest first, so READY_TO_VALIDATE wins over READY.
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


LEVEL_NAMES = frozenset(level.value for level in EvidenceLevel)
OVERALL_NAMES = frozenset(status.value for status in EnvironmentOverallStatus)
DOMAIN_NAMES = frozenset(domain.value for domain in ReadinessDomain)
STATUS_ALT = _alternation(LEVEL_NAMES | OVERALL_NAMES | {"NOT READY"})

LEVEL_RE = re.compile(rf"\b({_alternation(LEVEL_NAMES)})\b")
OVERALL_RE = re.compile(rf"\b({_alternation(OVERALL_NAMES | {'NOT READY'})})\b")
STATUS_RE = re.compile(rf"\b({STATUS_ALT})\b")
DOMAIN_RE = re.compile(rf"\b({_alternation(DOMAIN_NAMES)})\b")
# A sentence that ties a level to a domain rather than to the environment.
DOMAIN_QUALIFIER_RE = re.compile(
    rf"\b(?:{_alternation(DOMAIN_NAMES)})\b|\bdomain|\bGCS\b|\bBigQuery\b"
)

ENV_ALIASES = {
    "GCP_SANDBOX": EnvironmentName.GCP_SANDBOX,
    "GCP sandbox": EnvironmentName.GCP_SANDBOX,
    "Sandbox": EnvironmentName.GCP_SANDBOX,
    "sandbox": EnvironmentName.GCP_SANDBOX,
    "LOCAL": EnvironmentName.LOCAL,
    "DEV": EnvironmentName.DEV,
    "QA": EnvironmentName.QA,
    "PROD": EnvironmentName.PROD,
}
_ENV_NAME = _alternation(ENV_ALIASES)
_ENV = rf"(?:`|\*\*)?(?<![\w-])(?:{_ENV_NAME})(?![\w-])(?:`|\*\*)?"
# "A, B and C" / "A/B/C" / "A, B, and C": nothing continues after the final "and",
# so "in DEV and QA, PROD stays BLOCKED" does not read as one list.
_ENV_LIST = (
    rf"{_ENV}(?:\s*(?:,|/|&)\s*{_ENV})*(?:\s*(?:,\s*and|\band)\b\s*{_ENV})?"
)
_EVERY_ENV = r"(?:[Ee]very|[Ee]ach|[Aa]ll)\s+(?:cloud\s+)?environments?"

# "<envs> is/are/stay(s)/remain(s) [all|still|...] <status>"
SUBJECT_CLAIM_RE = re.compile(
    rf"(?P<envs>{_ENV_LIST}|{_EVERY_ENV})\s+"
    r"(?P<verb>is|are|stays?|remains?|becomes?|was|were)"
    r"(?:\s+(?:all|still|now|also|both|each|always|explicitly|therefore|overall|"
    r"today|currently))*"
    rf"\s+(?:\*\*)?`?(?P<status>{STATUS_ALT})\b"
)
SINGULAR_VERBS = frozenset({"is", "stays", "remains", "becomes", "was"})
# "<status> for/in <envs> [only]", "<status> (<envs>", and, only with "only",
# "<status>, <envs> only" / "<status> <envs> only".
LEVEL_FOR_ENV_RE = re.compile(
    rf"(?P<status>{STATUS_ALT})\b`?(?:\*\*)?"
    r"(?:(?P<prep>\s+(?:for|in)\s+(?:the\s+)?|\s+\()|(?P<bare>\s*,\s*|\s+))"
    rf"(?P<envs>{_ENV_LIST})(?P<only>(?:\*\*)?\s+only\b)?"
)
SKIP_SECTION_RE = re.compile(r"inconsisten|outdated|stale|superseded", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"local[- ]only placeholder", re.IGNORECASE)
HEADER_ENVS = {
    "LOCAL": EnvironmentName.LOCAL,
    "GCP_SANDBOX": EnvironmentName.GCP_SANDBOX,
    "GCP SANDBOX": EnvironmentName.GCP_SANDBOX,
    "SANDBOX": EnvironmentName.GCP_SANDBOX,
    "DEV": EnvironmentName.DEV,
    "QA": EnvironmentName.QA,
    "PROD": EnvironmentName.PROD,
}


# --- registry-derived truth -------------------------------------------------


@dataclass(frozen=True)
class Truth:
    levels: dict[tuple[EnvironmentName, ReadinessDomain], str]
    overall: dict[EnvironmentName, str]

    @classmethod
    def of(cls, registry: tuple[ReadinessEntry, ...] | None = None) -> Truth:
        rows = registry if registry is not None else build_readiness_registry()
        return cls(
            levels={(e.environment, e.domain): e.evidence_level.value for e in rows},
            overall={
                env: evaluate_environment_readiness(env, registry=rows).overall_status.value
                for env in EnvironmentName
            },
        )

    def levels_in(self, env: EnvironmentName) -> frozenset[str]:
        return frozenset(level for (e, _), level in self.levels.items() if e is env)

    def envs_with(self, domain: ReadinessDomain, level: str) -> frozenset[EnvironmentName]:
        return frozenset(e for (e, d), lv in self.levels.items() if d is domain and lv == level)

    def all_mandatory_blocked(self, env: EnvironmentName) -> bool:
        return all(
            self.levels[(env, d)] == EvidenceLevel.BLOCKED.value for d in MANDATORY_CLOUD_DOMAINS
        )


def _promoted(
    changes: dict[tuple[EnvironmentName, ReadinessDomain], EvidenceLevel],
) -> tuple[ReadinessEntry, ...]:
    """The real registry with some levels raised (synthetic; never the real state)."""
    rows = []
    for entry in build_readiness_registry():
        level = changes.get((entry.environment, entry.domain))
        if level is not None:
            entry = entry.model_copy(update={"evidence_level": level, "blocking": False})
        rows.append(entry)
    return tuple(rows)


# --- Markdown parsing -------------------------------------------------------


@dataclass(frozen=True)
class ParsedDoc:
    tables: tuple[tuple[tuple[str, ...], ...], ...]
    units: tuple[str, ...]  # prose sentences and non-matrix table cells


def _cells(line: str) -> tuple[str, ...]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return tuple(cell.strip() for cell in re.split(r"(?<!\\)\|", body))


def _plain(text: str) -> str:
    # Drop link targets: URLs and anchors are not claims.
    return re.sub(r"\]\([^)]*\)", "]", text)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.;!?])\s+", _plain(text)) if s.strip()]


def _clean(cell: str) -> str:
    return re.sub(r"[`*]", "", cell).strip()


def _matrix_columns(header: tuple[str, ...]) -> dict[int, EnvironmentName]:
    cols = {
        i: HEADER_ENVS[_clean(cell).upper()]
        for i, cell in enumerate(header)
        if _clean(cell).upper() in HEADER_ENVS
    }
    envs = set(cols.values())
    return cols if {EnvironmentName.GCP_SANDBOX, EnvironmentName.PROD} <= envs else {}


def parse_markdown(text: str) -> ParsedDoc:
    tables: list[tuple[tuple[str, ...], ...]] = []
    units: list[str] = []
    headings: list[tuple[int, str]] = []
    paragraph: list[str] = []
    table: list[tuple[str, ...]] = []
    in_fence = False

    def skipped() -> bool:
        return any(SKIP_SECTION_RE.search(title) for _, title in headings)

    def flush_paragraph() -> None:
        if paragraph and not skipped():
            units.extend(_sentences(" ".join(paragraph)))
        paragraph.clear()

    def flush_table() -> None:
        if table and not skipped():
            if _matrix_columns(table[0]):
                tables.append(tuple(table))
            else:
                units.extend(s for row in table for cell in row for s in _sentences(cell))
        table.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            flush_paragraph()
            flush_table()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        if heading:
            flush_paragraph()
            flush_table()
            depth = len(heading.group(1))
            while headings and headings[-1][0] >= depth:
                headings.pop()
            headings.append((depth, heading.group(2)))
            continue
        if line.startswith("|"):
            flush_paragraph()
            table.append(_cells(line))
            continue
        flush_table()
        if not line:
            flush_paragraph()
            continue
        if re.match(r"(?:[-*+]|\d+\.)\s", line):
            flush_paragraph()
        paragraph.append(line)
    flush_paragraph()
    flush_table()
    return ParsedDoc(tables=tuple(tables), units=tuple(units))


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


# --- checkers ---------------------------------------------------------------


def matrix_violations(
    table: tuple[tuple[str, ...], ...], truth: Truth, *, strict: bool
) -> list[str]:
    """Compare one readiness matrix with the registry, cell by cell."""
    cols = _matrix_columns(table[0])
    violations: list[str] = []
    covered: Counter[ReadinessDomain] = Counter()
    overall_seen = False
    for row in table[1:]:
        if all(re.fullmatch(r":?-{3,}:?", c) for c in row if c):
            continue
        label = _clean(row[0])
        if label.lower().startswith("overall"):
            overall_seen = True
            for i, env in cols.items():
                found = OVERALL_RE.search(_clean(row[i])) if i < len(row) else None
                if found is None:
                    if strict:
                        violations.append(f"Overall/{env.value}: no overall status")
                    continue
                got = found.group(1).replace(" ", "_")
                if got != truth.overall[env]:
                    violations.append(
                        f"Overall/{env.value}: doc {got}, registry {truth.overall[env]}"
                    )
            continue
        tokens = re.findall(r"[A-Z][A-Z0-9_]*[A-Z0-9]", label)
        domains = [ReadinessDomain(t) for t in tokens if t in DOMAIN_NAMES]
        if not domains:
            if strict:
                violations.append(f"row {label!r} names no registry domain")
            continue
        if strict and len(domains) != len(tokens):
            violations.append(f"row {label!r} mixes non-domain labels")
        covered.update(domains)
        for i, env in cols.items():
            cell = _clean(row[i]) if i < len(row) else ""
            found = LEVEL_RE.search(cell)
            if found is None:
                if strict:
                    violations.append(f"{label}/{env.value}: no evidence level in {cell!r}")
                continue
            for domain in domains:
                want = truth.levels[(env, domain)]
                if found.group(1) != want:
                    violations.append(
                        f"{domain.value}/{env.value}: doc {found.group(1)}, registry {want}"
                    )
    if strict:
        missing = [d.value for d in DOMAIN_INVENTORY if not covered[d]]
        repeated = sorted(d.value for d, n in covered.items() if n > 1)
        if missing:
            violations.append(f"matrix misses registry domains: {missing}")
        if repeated:
            violations.append(f"matrix repeats registry domains: {repeated}")
        if not overall_seen:
            violations.append("matrix has no Overall row")
    return violations


def _envs(text: str) -> tuple[EnvironmentName, ...]:
    if re.match(_EVERY_ENV, text):
        return tuple(EnvironmentName)
    names = re.findall(_ENV_NAME, text)
    return tuple(dict.fromkeys(ENV_ALIASES[name] for name in names))


def _subject_envs(match: re.Match[str]) -> tuple[EnvironmentName, ...]:
    envs = _envs(match["envs"])
    # "QA, PROD stays BLOCKED": a singular verb after a comma list binds to the last name.
    if match["verb"] in SINGULAR_VERBS and "," in match["envs"]:
        return envs[-1:]
    return envs


def _status_fits(truth: Truth, env: EnvironmentName, status: str, sentence: str) -> bool:
    if status == truth.overall[env]:
        return True
    if status == EvidenceLevel.BLOCKED.value and truth.all_mandatory_blocked(env):
        return True
    # A domain-level wording ("DEV is VERIFIED for GCS") is fine when some
    # domain really has that level there.
    return status in truth.levels_in(env) and DOMAIN_QUALIFIER_RE.search(sentence) is not None


def _level_claim_problems(
    truth: Truth,
    envs: tuple[EnvironmentName, ...],
    status: str,
    domains: list[ReadinessDomain],
    only: bool,
) -> list[str]:
    problems: list[str] = []
    if status in LEVEL_NAMES and domains:
        for domain in domains:
            for env in envs:
                want = truth.levels[(env, domain)]
                if want != status:
                    problems.append(f"{domain.value}/{env.value} is {want}, not {status}")
            if only and truth.envs_with(domain, status) != frozenset(envs):
                problems.append(f"{domain.value} is {status} beyond {[e.value for e in envs]}")
        return problems
    for env in envs:
        if status != truth.overall[env] and status not in truth.levels_in(env):
            problems.append(f"no {env.value} domain or overall status is {status}")
    if only and status in LEVEL_NAMES and not any(
        truth.envs_with(domain, status) == frozenset(envs) for domain in DOMAIN_INVENTORY
    ):
        problems.append(f"no domain is {status} in {[e.value for e in envs]} only")
    return problems


def claim_violations(units: Iterable[str], truth: Truth) -> list[str]:
    """Environment status/level claims in prose that the registry contradicts."""
    violations: list[str] = []
    for unit in units:
        text = _plain(unit)
        for match in SUBJECT_CLAIM_RE.finditer(text):
            status = match["status"].replace(" ", "_")
            envs = _subject_envs(match)
            wrong = [e.value for e in envs if not _status_fits(truth, e, status, text)]
            if wrong:
                violations.append(f"{match.group(0)!r}: registry disagrees for {wrong}")
        token_ends = [m.end() for m in STATUS_RE.finditer(text)]
        for match in LEVEL_FOR_ENV_RE.finditer(text):
            if match["bare"] is not None and not match["only"]:
                continue  # "<level>, PROD stays ..." starts a new clause
            start = match.start("status")
            previous = max((end for end in token_ends if end <= start), default=0)
            domains = [ReadinessDomain(d) for d in DOMAIN_RE.findall(text[previous:start])]
            problems = _level_claim_problems(
                truth,
                _envs(match["envs"]),
                match["status"].replace(" ", "_"),
                domains,
                bool(match["only"]),
            )
            if problems:
                violations.append(f"{match.group(0)!r}: {problems}")
    return violations


def stated_not_ready(units: Iterable[str]) -> set[EnvironmentName]:
    stated: set[EnvironmentName] = set()
    for unit in units:
        for match in SUBJECT_CLAIM_RE.finditer(_plain(unit)):
            if match["status"].replace(" ", "_") == EnvironmentOverallStatus.NOT_READY.value:
                stated.update(_subject_envs(match))
    return stated


def config_violations(path: str, text: str, entry: ReadinessEntry) -> list[str]:
    """A placeholder config must say so, stay local, and match the registry."""
    violations: list[str] = []
    comments = " ".join(
        line.split("#", 1)[1] for line in text.splitlines() if line.lstrip().startswith("#")
    )
    if not PLACEHOLDER_RE.search(comments):
        violations.append(f"{path}: no 'local-only placeholder' disclaimer comment")
    data = yaml.safe_load(text) or {}
    backend = (data.get("storage") or {}).get("backend")
    if backend != "local":
        violations.append(f"{path}: storage.backend is {backend!r}, not 'local'")
    if (data.get("cloud") or {}).get("project_id"):
        violations.append(f"{path}: sets cloud.project_id")
    if EvidenceLabel.PLACEHOLDER_CONFIG not in entry.evidence_labels:
        violations.append(
            f"registry {entry.environment.value} CONFIGURATION lost PLACEHOLDER_CONFIG"
        )
    if path not in entry.evidence or not PLACEHOLDER_RE.search(entry.evidence):
        violations.append(
            f"registry {entry.environment.value} CONFIGURATION evidence no longer calls "
            f"{path} a local-only placeholder"
        )
    return violations


def _readiness_matrix(text: str) -> tuple[tuple[str, ...], ...]:
    tables = parse_markdown(text).tables
    assert len(tables) == 1, f"expected one readiness matrix, found {len(tables)}"
    return tables[0]


# --- the real docs and configs ----------------------------------------------


def test_registry_state_these_checks_assume() -> None:
    # Every environment NOT_READY, PROD mandatory domains BLOCKED, no promotion.
    truth = Truth.of()
    assert set(truth.overall.values()) == {EnvironmentOverallStatus.NOT_READY.value}
    assert truth.all_mandatory_blocked(EnvironmentName.PROD)
    for source, target in zip(CLOUD_PROMOTION_ORDER, CLOUD_PROMOTION_ORDER[1:]):
        assert can_promote(source, target).allowed is False


def test_readiness_matrix_matches_registry_cell_by_cell() -> None:
    matrix = _readiness_matrix(_read(READINESS_DOC))
    assert matrix_violations(matrix, Truth.of(), strict=True) == []


@pytest.mark.parametrize("relative", STATUS_DOCS)
def test_any_other_readiness_matrix_matches_registry(relative: str) -> None:
    truth = Truth.of()
    for table in parse_markdown(_read(relative)).tables:
        assert matrix_violations(table, truth, strict=False) == [], relative


@pytest.mark.parametrize("relative", STATUS_DOCS)
def test_docs_never_promote_an_environment_beyond_the_registry(relative: str) -> None:
    assert claim_violations(parse_markdown(_read(relative)).units, Truth.of()) == []


@pytest.mark.parametrize("relative", STATUS_DOCS)
def test_docs_state_every_not_ready_cloud_environment(relative: str) -> None:
    truth = Truth.of()
    expected = {
        env
        for env in CLOUD_PROMOTION_ORDER
        if truth.overall[env] == EnvironmentOverallStatus.NOT_READY.value
    }
    assert expected <= stated_not_ready(parse_markdown(_read(relative)).units), relative


def test_readiness_legend_names_exactly_the_registry_verified_pairs() -> None:
    truth = Truth.of()
    lines = _read(READINESS_DOC).splitlines()
    verified_row = _plain(next(line for line in lines if line.startswith("| `VERIFIED` |")))
    verified = {key for key, level in truth.levels.items() if level == EvidenceLevel.VERIFIED.value}
    assert {ReadinessDomain(d) for d in DOMAIN_RE.findall(verified_row)} == {
        domain for _, domain in verified
    }
    assert set(_envs(verified_row)) == {env for env, _ in verified}
    rtv_row = next(line for line in lines if line.startswith("| `READY_TO_VALIDATE` |"))
    nobody_rtv = EvidenceLevel.READY_TO_VALIDATE.value not in set(truth.levels.values())
    assert ("no registry entry is at this level today" in rtv_row) is nobody_rtv


def test_readiness_doc_states_the_promotion_outcome() -> None:
    text = " ".join(_read(READINESS_DOC).split())
    denied = all(
        not can_promote(source, target).allowed
        for source, target in zip(CLOUD_PROMOTION_ORDER, CLOUD_PROMOTION_ORDER[1:])
    )
    assert ("`can_promote` denies every step" in text) is denied
    assert "(GCP_SANDBOX → DEV, DEV → QA, QA → PROD)" in text


@pytest.mark.parametrize("env", sorted(PLACEHOLDER_CONFIGS, key=lambda e: e.value))
def test_placeholder_configs_keep_their_local_only_disclaimer(env: EnvironmentName) -> None:
    path = PLACEHOLDER_CONFIGS[env]
    entry = next(
        e
        for e in build_readiness_registry()
        if e.environment is env and e.domain is ReadinessDomain.CONFIGURATION
    )
    assert config_violations(path, _read(path), entry) == []


# --- negative cases: the same checkers must fail on promoted or stale text ----


def test_matrix_check_fails_when_a_doc_cell_is_promoted() -> None:
    # The pre-#123 GCS row: DEV/QA READY_TO_VALIDATE although the registry says VERIFIED.
    stale = (
        "| Domain | LOCAL | GCP_SANDBOX | DEV | QA | PROD |\n"
        "|---|---|---|---|---|---|\n"
        "| GCS_RAW_LANDING | DEMONSTRATED_LOCAL | VERIFIED (#91 live smoke; sandbox only) "
        "| READY_TO_VALIDATE | READY_TO_VALIDATE | BLOCKED (PROD) |\n"
        "| CONFIGURATION | DEMONSTRATED_LOCAL | CONTRACT_DEFINED | CONTRACT_DEFINED "
        "| CONTRACT_DEFINED | VERIFIED |\n"
        "| **Overall** | NOT_READY | NOT_READY | READY_TO_VALIDATE | NOT_READY | READY |\n"
    )
    (table,) = parse_markdown(stale).tables
    violations = matrix_violations(table, Truth.of(), strict=False)
    assert "GCS_RAW_LANDING/DEV: doc READY_TO_VALIDATE, registry VERIFIED" in violations
    assert "GCS_RAW_LANDING/QA: doc READY_TO_VALIDATE, registry VERIFIED" in violations
    assert "CONFIGURATION/PROD: doc VERIFIED, registry BLOCKED" in violations
    assert "Overall/DEV: doc READY_TO_VALIDATE, registry NOT_READY" in violations
    assert "Overall/PROD: doc READY, registry NOT_READY" in violations
    strict = matrix_violations(table, Truth.of(), strict=True)
    assert any(v.startswith("matrix misses registry domains") for v in strict)


def test_matrix_and_claims_follow_a_promoted_registry() -> None:
    # If the registry raised every DEV mandatory domain, today's docs would be
    # wrong; the checks must notice rather than pin today's wording.
    promoted = Truth.of(
        _promoted(
            {(EnvironmentName.DEV, d): EvidenceLevel.VERIFIED for d in MANDATORY_CLOUD_DOMAINS}
        )
    )
    assert promoted.overall[EnvironmentName.DEV] == EnvironmentOverallStatus.READY.value
    text = _read(READINESS_DOC)
    matrix = matrix_violations(_readiness_matrix(text), promoted, strict=True)
    assert "Overall/DEV: doc NOT_READY, registry READY" in matrix
    assert "CONFIGURATION/DEV: doc CONTRACT_DEFINED, registry VERIFIED" in matrix
    claims = claim_violations(parse_markdown(text).units, promoted)
    assert any("DEV" in v and "NOT_READY" in v for v in claims)


def test_matrix_check_follows_one_promoted_prod_domain() -> None:
    promoted = Truth.of(
        _promoted({(EnvironmentName.PROD, ReadinessDomain.GCS_RAW_LANDING): EvidenceLevel.VERIFIED})
    )
    matrix = matrix_violations(_readiness_matrix(_read(READINESS_DOC)), promoted, strict=True)
    assert matrix == ["GCS_RAW_LANDING/PROD: doc BLOCKED, registry VERIFIED"]


@pytest.mark.parametrize(
    "sentence",
    [
        # Overall promotions.
        "DEV and QA are `READY_TO_VALIDATE` overall.",
        "QA is `READY`.",
        "Every environment stays `READY_TO_VALIDATE`.",
        "DEV is `VERIFIED`.",
        # PROD anything but BLOCKED / NOT_READY.
        "PROD is `VERIFIED` for GCS.",
        "PROD is `READY_TO_VALIDATE`.",
        "`BIGQUERY_ANALYTICAL` is `VERIFIED` for PROD.",
        # Stale wording from before #123 (environment-readiness.md, README.md, roadmap.md).
        "Therefore `GCS_RAW_LANDING` is `VERIFIED` for **GCP_SANDBOX only**, and "
        "`READY_TO_VALIDATE` for DEV/QA; PROD remains BLOCKED.",
        "GCS (#9) and the BigQuery adapter (#10) are `VERIFIED` in GCP_SANDBOX only "
        "(#91/#92 live smokes; operator-owned project, user ADC), `READY_TO_VALIDATE` "
        "for DEV/QA, and `BLOCKED` in PROD.",
        "GCS passed a live sandbox smoke (#91): `VERIFIED` for GCP_SANDBOX only "
        "(operator-owned project, user ADC; DEV/QA stay `READY_TO_VALIDATE`).",
        "| 03 | Storage | live sandbox smoke passed in #91 (`VERIFIED`, GCP_SANDBOX only) |",
        "| 04 | Warehouse | live sandbox smoke #92; `VERIFIED` GCP_SANDBOX only |",
        "DEV/QA is `READY_TO_VALIDATE`.",
        "`BIGQUERY_ANALYTICAL` stays `VERIFIED` for **GCP_SANDBOX only**. DEV/QA stay "
        "`READY_TO_VALIDATE`, PROD stays `BLOCKED`.",
    ],
)
def test_claim_check_flags_promoted_and_stale_statements(sentence: str) -> None:
    assert claim_violations(parse_markdown(sentence).units, Truth.of()) != []


@pytest.mark.parametrize(
    "sentence",
    [
        "Every environment is `NOT_READY` overall, and PROD stays `BLOCKED`.",
        "Today Sandbox/DEV/QA/PROD are all `NOT_READY` overall.",
        "`GCS_RAW_LANDING` and `BIGQUERY_ANALYTICAL` are `VERIFIED` in GCP_SANDBOX, DEV "
        "and QA, at domain level only.",
        "DEV and QA are `VERIFIED` for GCS and BigQuery at the domain level only.",
        "`DATA_QUALITY` stays `CLOUD_UNVERIFIED` in GCP_SANDBOX, DEV and QA.",
        "`can_promote()` needs a `READY` source and a target that is `READY_TO_VALIDATE` "
        "or `READY`.",
        "PROD remains NOT READY while mandatory cloud evidence is missing.",
        "`GCS_RAW_LANDING` is `VERIFIED` in DEV and QA, PROD stays `BLOCKED`.",
        "In DEV and QA both domains are `VERIFIED`, PROD stays `BLOCKED`.",
        "`VERIFIED` (GCP_SANDBOX, adapter contract only)",
    ],
)
def test_claim_check_accepts_truthful_statements(sentence: str) -> None:
    assert claim_violations(parse_markdown(sentence).units, Truth.of()) == []


def test_sections_that_quote_stale_statements_are_skipped() -> None:
    text = (
        "## Known inconsistencies in the docs\n\n"
        "| Statement | Where |\n| --- | --- |\n"
        "| DEV/QA stay `READY_TO_VALIDATE` | roadmap |\n\n"
        "DEV/QA stay `READY_TO_VALIDATE`.\n\n"
        "## Open issues\n\n"
        "DEV/QA stay `READY_TO_VALIDATE`.\n"
    )
    assert len(claim_violations(parse_markdown(text).units, Truth.of())) == 1


def test_not_ready_statement_is_required() -> None:
    assert stated_not_ready(parse_markdown("PROD stays `BLOCKED`.").units) == set()
    stated = stated_not_ready(
        parse_markdown("GCP_SANDBOX, DEV, QA, and PROD are all `NOT_READY`.").units
    )
    assert stated == set(CLOUD_PROMOTION_ORDER)


@pytest.mark.parametrize(
    "text, problem",
    [
        ("environment: dev\nstorage:\n  backend: local\n", "disclaimer"),
        (
            "# Local-only placeholder.\nenvironment: dev\nstorage:\n  backend: gcs\n",
            "storage.backend",
        ),
        (
            "# Local-only placeholder.\nenvironment: dev\nstorage:\n  backend: local\n"
            "cloud:\n  project_id: example\n",
            "project_id",
        ),
    ],
)
def test_config_check_flags_a_promoted_placeholder(text: str, problem: str) -> None:
    entry = next(
        e
        for e in build_readiness_registry()
        if e.environment is EnvironmentName.DEV and e.domain is ReadinessDomain.CONFIGURATION
    )
    violations = config_violations("config/dev.yaml", text, entry)
    assert any(problem in v for v in violations), violations


def test_config_check_flags_a_registry_that_drops_the_placeholder() -> None:
    entry = next(
        e
        for e in build_readiness_registry()
        if e.environment is EnvironmentName.QA and e.domain is ReadinessDomain.CONFIGURATION
    )
    promoted = entry.model_copy(
        update={"evidence": "config/qa.yaml configures QA", "evidence_labels": ()}
    )
    violations = config_violations("config/qa.yaml", _read("config/qa.yaml"), promoted)
    assert len(violations) == 2
