"""Guard the architecture decision records in ``docs/adr/`` (#124, DE-03).

Every ``NNNN-kebab-title.md`` file in ``docs/adr/`` is an architecture decision
record (ADR), and ``docs/adr/README.md`` is their index and rulebook. These
offline checks keep each ADR in the shape the index prescribes and keep it
evidence-honest:

- front matter ``id`` equals the file stem, one ``# ADR NNNN: Title`` heading,
  and exactly the required H2 sections in the required order;
- one status from the closed vocabulary, with the lead architect's acceptance
  rule;
- every ``### Dn`` decision carries the bold labels ``**Decision**:`` through
  ``**Evidence**:`` once each, in order, names its DE issues with links, and
  has at least one row with a revisit trigger in the ``Not selected`` table;
- DE planning IDs match their GitHub issues (DE-*k* is #(121+*k*)), issue and
  PR links resolve to this repository, relative links and ``#anchors``
  resolve, and cited register rows exist;
- each ADR's register-row table lists exactly the register rows that cite the
  ADR in Paths or still name its issue as a follow-on, and its "Still open"
  cells name exactly the DE issues those rows follow;
- no private identifiers (emails, bucket URLs, identities, IAM members,
  project IDs, run IDs not already tracked in the recorded-run docs), no
  currency amounts or unit prices, and no claim of a deployment, enterprise
  evidence or an SLA;
- the index lists every ADR exactly once and agrees with its title, status and
  date, the docs site sidebar shows every ADR page, and every link from the
  README or the docs into an ADR heading resolves.

Negative cases feed broken ADR and index text to the same validators.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs/adr"
INDEX = ADR_DIR / "README.md"
REGISTER = REPO_ROOT / "docs/requirements/openalex-lead-coverage.md"
SIDEBARS = REPO_ROOT / "docs/site/sidebars.ts"
REPO_URL = "https://github.com/agilandeenadhayalan41/research-intelligence-data-platform"

REQUIRED_SECTIONS = (
    "Status",
    "Context",
    "Decisions",
    "Ownership and sign-off",
    "Cost model",
    "Not selected",
    "Lead requirement traceability",
    "Technology selection scorecard (lead Section 24)",
    "Consequences and follow-on work",
    "Rollback",
    "References",
)
STATUSES = frozenset({"Proposed", "Accepted", "Superseded", "Deprecated"})
DECISION_LABELS = (
    "Decision",
    "Options considered",
    "Rationale",
    "Consequences",
    "Not selected",
    "Implemented by",
    "Evidence",
)
NOT_SELECTED_COLUMNS = ("Technology or option", "Decision", "Revisit trigger", "Owner DE issue")
INDEX_COLUMNS = ("ADR", "Title", "Status", "Recorded", "Issue")
# A trigger cell must say what reopens the option; "None: <why>" is fine.
PLACEHOLDER_TRIGGERS = frozenset(
    {"", "—", "–", "-", "?", "tbd", "tba", "todo", "n/a", "na", "none", "never"}
)
# Lead Section 24 criteria per layer, in the lead's words.
LEAD_SECTION_24 = {
    "Canonical storage": (
        "cost",
        "durability",
        "open formats",
        "portability",
        "lifecycle management",
        "schema evolution",
        "integration",
    ),
    "Analytical engine": (
        "large join performance",
        "COUNT DISTINCT performance",
        "data scanned",
        "concurrency",
        "query latency",
        "cost",
        "partition pruning",
        "clustering",
        "materialization capability",
        "operational overhead",
        "security/governance",
    ),
    "Operational store": (
        "point lookup latency",
        "concurrency",
        "index support",
        "cost",
        "data size",
        "update behavior",
        "availability requirements",
        "operational overhead",
    ),
}
# DE-01..DE-35 were created as issues #122..#156 (DE-k is #121+k).
DE_ISSUE_OFFSET = 121
DE_COUNT = 35

ADR_FILE_RE = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
H1_RE = re.compile(r"^# (.*)$", re.MULTILINE)
H2_RE = re.compile(r"^## (.*?)\s*$", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
DECISION_RE = re.compile(r"^### D(\d+) \S.*$", re.MULTILINE)
# The ADRs write decision labels as **Label**: at the start of a line.
LABEL_RE = re.compile(r"^\*\*([^*\n]+)\*\*:", re.MULTILINE)
STATUS_RE = re.compile(r"^(?:- )?\*\*Status(?::\*\*|\*\*:)[ \t]*(\S+?)[.,;]?(?:[ \t](.*))?$", re.MULTILINE)
RECORDED_RE = re.compile(r"\*\*Recorded(?::\*\*|\*\*:)\s*(\d{4}-\d{2}-\d{2})")
DE_RE = re.compile(r"\bDE-(\d+)\b(?:\s*\[#(\d+)\])?")
LINK_FIRST_DE_RE = re.compile(r"\[#(\d+)\]\s*\(DE-(\d+)\)")
OWNER_RE = re.compile(r"^DE-(\d{2}) \[#(\d+)\]$")
DECISION_REF_RE = re.compile(r"\bD(\d+)(?:\s*[–-]\s*D?(\d+))?\b")
REF_DEF_RE = re.compile(r"^\[#(\d+)\]: (\S+)\s*$", re.MULTILINE)
REF_USE_RE = re.compile(r"\[#(\d+)\]")
MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
INBOUND_ADR_LINK_RE = re.compile(r"\]\(((?:[^)\s#]*/)?adr/(\d{4}-[a-z0-9-]+\.md))#([^)\s]+)\)")
REQ_ID_RE = re.compile(r"\bL\d{2}(?:\.\d)?-\d{2}\b")
BACKTICK_RE = re.compile(r"`[^`\n]*`")

PRIVATE_PATTERNS = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "an email address"),
    (re.compile(r"\bgs://", re.IGNORECASE), "a gs:// bucket URL"),
    (re.compile(r"\.iam\.gserviceaccount\.com\b", re.IGNORECASE), "a service-account identity"),
    (re.compile(r"\bprojects/[a-z][a-z0-9-]{4,28}[a-z0-9]\b"), "a projects/<id> project ID"),
    (re.compile(r"\b(?:serviceAccount|user|group):[A-Za-z0-9._%+-]+@"), "an IAM member"),
)
# Operator run IDs (for example i103-20261012t180931) may appear only if a
# recorded-run doc outside docs/adr/ already publishes them.
RUN_ID_RE = re.compile(r"\bi\d{3}-20\d{6}(?:t\d{6})?\b", re.IGNORECASE)
CURRENCY_WORD_RE = re.compile(r"[$€£¥]|\b(?:USD|EUR|GBP|dollars?|cents?)\b", re.IGNORECASE)
CURRENCY_AMOUNT_RE = re.compile(
    r"[$€£¥]\s*\d|\b\d[\d,.]*\s*(?:USD|EUR|GBP)\b|\b(?:USD|EUR|GBP)\s*\d", re.IGNORECASE
)
# A bare number directly followed by a billing unit is a unit price
# ("6.25 per TiB", "0.02 per GB-month", "1.20/hour"); "10 MiB per table" is not.
PRICE_RATE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:per|/)\s*(?:GiB|GB|TiB|TB|vCPU|hour|month)\b", re.IGNORECASE
)
# Affirmative claims an ADR must never make: it is a decision, not evidence.
CLAIM_PATTERNS = (
    (
        re.compile(r"\b(?:ENTERPRISE_DEV_VERIFIED|ENTERPRISE_QA_VERIFIED|PROD_DEPLOYED)\b"),
        "an unproven evidence label",
    ),
    (
        re.compile(
            r"(?<![Nn]othing )(?<![Nn]one )\b(?:is|are|was|were|has been|have been)\s+"
            r"(?:now\s+|already\s+)?(?:deployed|live|running)\s+(?:to|in|on)\b"
        ),
        "a deployment",
    ),
    (re.compile(r"\b(?:is|are)\s+(?:now\s+)?in\s+production\b"), "a deployment"),
    (re.compile(r"(?<!not )(?<!never )\bproduction[- ]ready\b", re.IGNORECASE), "production readiness"),
    (
        re.compile(
            r"\b(?:verified|validated|accepted|measured|tested|passed|proven|ran)\s+in\s+"
            r"(?:an?\s+|the\s+)?enterprise\b",
            re.IGNORECASE,
        ),
        "enterprise evidence",
    ),
    (
        re.compile(
            r"\benterprise\s+evidence\s+(?:shows|proves|confirms|exists|demonstrates)\b", re.IGNORECASE
        ),
        "enterprise evidence",
    ),
    (
        re.compile(
            r"(?<!-)\b(?:meets?|met|meeting|guarantees?|guaranteed|signed|agreed|validated|committed)\s+"
            r"(?:to\s+)?(?:the\s+|an?\s+|its\s+|our\s+)?(?:[\w/-]+\s+){0,3}SLAs?\b"
        ),
        "an SLA",
    ),
    (
        re.compile(
            r"\bSLAs?\s+(?:is|are|was|were|has been|have been)\s+"
            r"(?:met|agreed|signed|guaranteed|validated|in place|defined)\b"
        ),
        "an SLA",
    ),
    (re.compile(r"\b\d+\s*ms\s+(?:p\d+\s+)?SLA\b"), "an SLA"),
)


# --- Parsing helpers -------------------------------------------------------------


def _split_cells(line: str) -> tuple[str, ...]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return tuple(cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body))


def _without_fences(text: str) -> str:
    """Blank fenced code blocks and front matter, keeping line numbers."""
    lines = text.split("\n")
    match = FRONT_MATTER_RE.match(text)
    skip = match.group(0).count("\n") if match else 0
    out, fence = [], None
    for index, line in enumerate(lines):
        opener = FENCE_RE.match(line)
        if index < skip:
            out.append("")
        elif fence is not None:
            out.append("")
            if opener and opener.group(1) == fence:
                fence = None
        elif opener:
            fence = opener.group(1)
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    if match is None:
        return ""
    rest = text[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    return rest if following is None else rest[: following.start()]


def _tables(body: str) -> list[tuple[tuple[str, ...], list[tuple[str, ...]]]]:
    """Every Markdown table in ``body`` as (header cells, data rows)."""
    tables, block = [], []
    for line in [*body.splitlines(), ""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if len(block) >= 2:
            tables.append((_split_cells(block[0]), [_split_cells(row) for row in block[2:]]))
        block = []
    return tables


def _decision_refs(cell: str) -> set[int]:
    refs: set[int] = set()
    for start, end in DECISION_REF_RE.findall(cell):
        refs.update(range(int(start), int(end or start) + 1))
    return refs


def _slug(heading: str) -> str:
    """The anchor GitHub and Docusaurus (github-slugger) give a heading."""
    custom = re.search(r"\{#([^}]+)\}\s*$", heading)
    if custom:
        return custom.group(1)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text).lower()
    kept = "".join(ch for ch in text if ch in "-_ " or unicodedata.category(ch)[0] in "LMN")
    return kept.replace(" ", "-")


def heading_anchors(text: str) -> frozenset[str]:
    occurrences: dict[str, int] = {}
    for line in _without_fences(text).splitlines():
        match = HEADING_RE.match(line)
        if match is None:
            continue
        original = result = _slug(match.group(2))
        while result in occurrences:
            occurrences[original] += 1
            result = f"{original}-{occurrences[original]}"
        occurrences[result] = 0
    return frozenset(occurrences)


@cache
def _file_anchors(path: Path) -> frozenset[str]:
    return heading_anchors(path.read_text(encoding="utf-8"))


@cache
def _tracked_run_ids(repo_root: Path) -> frozenset[str]:
    """Run IDs that the docs outside ``docs/adr/`` (the recorded runs) already publish."""
    ids: set[str] = set()
    for page in sorted((repo_root / "docs").glob("*/*.md")):
        if page.parent.name not in {"adr", "site"}:
            ids.update(run.lower() for run in RUN_ID_RE.findall(page.read_text(encoding="utf-8")))
    return frozenset(ids)


def adr_title(text: str) -> str | None:
    match = re.search(r"^# ADR \d{4}: (\S.*)$", _without_fences(text), re.MULTILINE)
    return None if match is None else match.group(1).strip()


def adr_status(text: str) -> str | None:
    matches = STATUS_RE.findall(_section(_without_fences(text), "Status"))
    return matches[0][0] if len(matches) == 1 else None


def adr_recorded(text: str) -> str | None:
    match = RECORDED_RE.search(_section(_without_fences(text), "Status"))
    return None if match is None else match.group(1)


def register_cells(text: str, column: str) -> dict[str, str]:
    """Register row ID -> its cell in ``column`` ("" when the column is missing)."""
    header = next(
        (_split_cells(line) for line in text.splitlines() if line.startswith("| ID |")), ()
    )
    index = header.index(column) if column in header else None
    rows = {}
    for line in text.splitlines():
        if line.startswith("| L"):
            cells = _split_cells(line)
            rows[cells[0]] = cells[index] if index is not None and len(cells) > index else ""
    return rows


def register_follow_ons(text: str) -> dict[str, str]:
    """Register row ID -> Follow-on cell."""
    return register_cells(text, "Follow-on")


# --- Validators --------------------------------------------------------------------


def document_errors(text: str, path: Path, repo_root: Path) -> list[str]:
    """Checks shared by every file in docs/adr/: links, IDs, identifiers, claims."""
    errors: list[str] = []
    definitions: dict[int, str] = {}
    for number, url in REF_DEF_RE.findall(text):
        if url not in {f"{REPO_URL}/issues/{number}", f"{REPO_URL}/pull/{number}"}:
            errors.append(f"reference [#{number}] points at {url}")
        definitions[int(number)] = url
    prose = REF_DEF_RE.sub("", text)
    for number in sorted({int(n) for n in REF_USE_RE.findall(prose)}):
        if number not in definitions:
            errors.append(f"[#{number}] is used but has no link definition")

    pairs = [(de, number) for de, number in DE_RE.findall(prose)]
    pairs += [(de, number) for number, de in LINK_FIRST_DE_RE.findall(prose)]
    for de, number in pairs:
        k = int(de)
        if len(de) != 2 or not 1 <= k <= DE_COUNT:
            errors.append(f"DE-{de} is outside DE-01..DE-{DE_COUNT}")
        elif number and int(number) != DE_ISSUE_OFFSET + k:
            errors.append(f"DE-{de} is #{DE_ISSUE_OFFSET + k}, not #{number}")
        elif number and "/issues/" not in definitions.get(int(number), "/issues/"):
            errors.append(f"DE-{de} [#{number}] must link to /issues/")

    directory, root = path.parent, repo_root.resolve()
    own_anchors = heading_anchors(text)
    for target in MD_LINK_RE.findall(BACKTICK_RE.sub("", text)):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
            continue
        relative, _, anchor = target.partition("#")
        if not relative:
            if anchor not in own_anchors:
                errors.append(f"anchor #{anchor} does not exist in this page")
            continue
        resolved = (directory / relative).resolve()
        if not resolved.is_relative_to(root) or not resolved.exists():
            errors.append(f"relative link {target} does not resolve")
        elif anchor and resolved.suffix == ".md" and anchor not in _file_anchors(resolved):
            errors.append(f"anchor #{anchor} does not exist in {relative}")

    for pattern, what in PRIVATE_PATTERNS:
        for match in pattern.finditer(text):
            errors.append(f"private identifier ({what}): {match.group(0)}")
    tracked = _tracked_run_ids(root)
    for match in RUN_ID_RE.finditer(text):
        if match.group(0).lower() not in tracked:
            errors.append(f"private identifier (an untracked run ID): {match.group(0)}")
    unquoted = BACKTICK_RE.sub("", text)
    for match in CURRENCY_AMOUNT_RE.finditer(unquoted):
        errors.append(f"currency amount {match.group(0)!r}: ADRs carry no prices")
    for match in PRICE_RATE_RE.finditer(unquoted):
        errors.append(f"unit price {match.group(0)!r}: ADRs carry no prices")
    flat = re.sub(r"\s+", " ", text)
    for pattern, what in CLAIM_PATTERNS:
        for match in pattern.finditer(flat):
            errors.append(f"claims {what}: {match.group(0)!r}")
    return errors


def validate_adr(
    text: str,
    path: Path,
    repo_root: Path,
    *,
    register_ids: frozenset[str] | None = None,
) -> list[str]:
    """Return every rule violation in one ADR file (empty when valid)."""
    errors = document_errors(text, path, repo_root)
    name = ADR_FILE_RE.match(path.name)
    if name is None:
        return [*errors, f"{path.name}: file name must be NNNN-kebab-title.md"]
    number = name.group(1)

    front = FRONT_MATTER_RE.match(text)
    meta = yaml.safe_load(front.group(1)) if front else None
    if not isinstance(meta, dict) or meta.get("id") != path.stem:
        errors.append(f"front matter id must be {path.stem!r} (Docusaurus strips the number otherwise)")

    body = _without_fences(text)
    h1 = H1_RE.findall(body)
    if len(h1) != 1 or re.fullmatch(rf"ADR {number}: \S.*", h1[0].strip()) is None:
        errors.append(f"needs exactly one H1 '# ADR {number}: Title', found {h1}")
    h2 = tuple(H2_RE.findall(body))
    if h2 != REQUIRED_SECTIONS:
        errors.append(f"H2 sections must be {list(REQUIRED_SECTIONS)} in order, found {list(h2)}")

    status_body = _section(body, "Status")
    statuses = STATUS_RE.findall(status_body)
    if len(statuses) != 1:
        errors.append("Status needs exactly one '**Status:**' line")
    else:
        status, rest = statuses[0]
        if status not in STATUSES:
            errors.append(f"unknown status {status!r}; use one of {sorted(STATUSES)}")
        if status == "Superseded":
            successor = re.match(r"by ADR (\d{4})\b", rest or "")
            if successor is None:
                errors.append("Superseded needs 'by ADR NNNN'")
            elif not list(path.parent.glob(f"{successor.group(1)}-*.md")):
                errors.append(f"superseding ADR {successor.group(1)} does not exist")
        if status == "Deprecated" and not (rest or "").strip(" ."):
            errors.append("Deprecated needs a reason on the status line")
    if "lead architect" not in status_body.casefold():
        errors.append("Status must state the lead architect's acceptance rule")
    if RECORDED_RE.search(status_body) is None:
        errors.append("Status needs a '**Recorded:** YYYY-MM-DD' date")

    decisions_body = _section(body, "Decisions")
    headings = list(DECISION_RE.finditer(decisions_body))
    numbers = [int(match.group(1)) for match in headings]
    if not numbers:
        errors.append("Decisions has no '### Dn <title>' decision")
    elif numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"decisions must be numbered D1..D{len(numbers)} in order, found {numbers}")
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(decisions_body)
        errors.extend(_decision_errors(int(heading.group(1)), decisions_body[heading.end() : end]))

    errors.extend(_not_selected_errors(_section(body, "Not selected"), set(numbers)))

    trace = _section(body, "Lead requirement traceability")
    traced: set[int] = set()
    for header, rows in _tables(trace):
        if header and header[0] == "Decision":
            for row in rows:
                traced |= _decision_refs(row[0])
    for missing in sorted(set(numbers) - traced):
        errors.append(f"D{missing} is not in the traceability table")
    if register_ids is not None:
        for row_id in sorted(set(REQ_ID_RE.findall(body)) - register_ids):
            errors.append(f"register row {row_id} does not exist")

    errors.extend(_scorecard_errors(_section(body, "Technology selection scorecard (lead Section 24)")))

    owners = _section(body, "Ownership and sign-off")
    roles = {
        row[0] for header, rows in _tables(owners) if header and header[0] == "Role" for row in rows
    }
    if "Lead architect" not in roles:
        errors.append("Ownership and sign-off needs a role table with the Lead architect")

    cost = _section(body, "Cost model")
    if "No prices" not in cost:
        errors.append("Cost model must say 'No prices'")
    for match in CURRENCY_WORD_RE.finditer(cost):
        errors.append(f"Cost model contains currency {match.group(0)!r}")
    return errors


def _decision_errors(number: int, body: str) -> list[str]:
    where = f"D{number}"
    errors = []
    labels = list(LABEL_RE.finditer(body))
    names = [match.group(1) for match in labels]
    content: dict[str, str] = {}
    for index, match in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(body)
        content.setdefault(match.group(1), body[match.end() : end].strip())
    counts = Counter(names)
    for label in DECISION_LABELS:
        if counts[label] == 0:
            errors.append(f"{where}: missing label **{label}**:")
        elif counts[label] > 1:
            errors.append(f"{where}: label **{label}**: appears {counts[label]} times")
        elif not content[label]:
            errors.append(f"{where}: label **{label}**: is empty")
    if [name for name in names if name in DECISION_LABELS] != [
        label for label in DECISION_LABELS if counts[label]
    ]:
        errors.append(f"{where}: labels must follow the order {list(DECISION_LABELS)}")
    implemented = content.get("Implemented by", "")
    ids = DE_RE.findall(implemented)
    if implemented and not ids:
        errors.append(f"{where}: Implemented by names no DE issue")
    for de, link in ids:
        if not link:
            errors.append(f"{where}: DE-{de} in Implemented by needs its issue link")
    evidence = content.get("Evidence", "")
    if evidence and "](" not in evidence and "not yet measured" not in evidence:
        errors.append(f"{where}: Evidence needs a repository link or 'not yet measured'")
    return errors


def _not_selected_errors(body: str, decisions: set[int]) -> list[str]:
    tables = [table for table in _tables(body) if table[0] == NOT_SELECTED_COLUMNS]
    if not tables:
        return [f"Not selected needs a table with columns {list(NOT_SELECTED_COLUMNS)}"]
    errors, covered = [], set()
    rows = [row for _, table_rows in tables for row in table_rows]
    if not rows:
        errors.append("Not selected table has no rows")
    for row in rows:
        if len(row) != len(NOT_SELECTED_COLUMNS):
            errors.append(f"Not selected row {row[0]!r}: expected {len(NOT_SELECTED_COLUMNS)} cells")
            continue
        option, decision, trigger, owner = row
        where = f"Not selected row {option!r}"
        if not option:
            errors.append("Not selected row with an empty option")
        refs = _decision_refs(decision)
        if not refs:
            errors.append(f"{where}: Decision cell names no Dn")
        for ref in sorted(refs - decisions):
            errors.append(f"{where}: D{ref} is not a decision of this ADR")
        covered |= refs
        if trigger.strip(" .").casefold() in PLACEHOLDER_TRIGGERS:
            errors.append(f"{where}: empty revisit trigger {trigger!r}")
        if OWNER_RE.match(owner) is None:
            errors.append(f"{where}: owner must be 'DE-k [#n]', found {owner!r}")
    for missing in sorted(decisions - covered):
        errors.append(f"D{missing} has no row in the Not selected table")
    return errors


def _scorecard_errors(body: str) -> list[str]:
    if body.strip().startswith("Not applicable"):
        return []
    tables = [table for table in _tables(body) if table[0][:2] == ("Layer", "Criterion")]
    if not tables:
        return ["scorecard needs a Layer/Criterion table, or a line starting 'Not applicable'"]
    errors = []
    seen: Counter[tuple[str, str]] = Counter()
    for _, rows in tables:
        for row in rows:
            layer, criterion = row[0], row[1]
            if criterion not in LEAD_SECTION_24.get(layer, ()):
                errors.append(f"scorecard row ({layer}, {criterion}) is not a lead §24 criterion")
            seen[(layer, criterion)] += 1
            if not DE_RE.search(row[-1]):
                errors.append(f"scorecard row ({layer}, {criterion}): 'Measured by' names no DE issue")
    for pair, count in seen.items():
        if count > 1:
            errors.append(f"scorecard row {pair} appears {count} times")
    for layer in sorted({layer for layer, _ in seen} & set(LEAD_SECTION_24)):
        for criterion in LEAD_SECTION_24[layer]:
            if (layer, criterion) not in seen:
                errors.append(f"scorecard misses {layer} criterion {criterion!r}")
    return errors


def traceability_errors(adr_text: str, adr_name: str, issue: str, register: str) -> list[str]:
    """Pair an ADR's register-row table with the register rows that cite it.

    ``issue`` is the ADR's index cell, for example ``DE-03 [#124]``. A register
    row belongs in the ADR's ``Row`` table when its Follow-on names that issue
    or its Paths cell cites ``docs/adr/<adr_name>``; every row in the table
    exists in the register and does one of the two; and each table line's
    "Still open" cell names exactly the DE ids that its rows still follow (the
    ADR's own DE id excluded), so the two pages cannot drift apart.
    """
    follow_ons = register_cells(register, "Follow-on")
    paths = register_cells(register, "Paths")
    own = issue.split()[0]
    citing = {row for row, cell in paths.items() if f"docs/adr/{adr_name}" in cell}
    naming = {row for row, cell in follow_ons.items() if issue in cell}
    trace = _section(_without_fences(adr_text), "Lead requirement traceability")
    errors: list[str] = []
    answered: set[str] = set()
    for header, rows in _tables(trace):
        if not header or header[0] != "Row":
            continue
        still_open = next((i for i, cell in enumerate(header) if cell.startswith("Still open")), None)
        if still_open is None:
            errors.append("the register-row table has no 'Still open' column")
            continue
        for row in rows:
            ids = REQ_ID_RE.findall(row[0])
            answered.update(ids)
            named = {f"DE-{int(de):02d}" for de, _ in DE_RE.findall(row[still_open])}
            followed: set[str] = set()
            for row_id in ids:
                if row_id not in follow_ons:
                    errors.append(f"{row_id} is not a register row")
                    continue
                if row_id not in citing | naming:
                    errors.append(f"{row_id} neither cites docs/adr/{adr_name} nor names {issue}")
                mine = {f"DE-{int(de):02d}" for de, _ in DE_RE.findall(follow_ons[row_id])} - {own}
                if mine - named:
                    errors.append(f"{row_id} follows {sorted(mine - named)} not named as still open")
                followed |= mine
            if named - followed:
                errors.append(f"{', '.join(ids)}: still open names {sorted(named - followed)} no row follows")
    errors.extend(
        f"{row_id} cites or names this ADR but is not in its register-row table"
        for row_id in sorted((citing | naming) - answered)
    )
    return errors


def inbound_adr_anchor_errors(text: str, page: Path, adr_dir: Path) -> list[str]:
    """Links from ``page`` into an ADR heading (``.../adr/NNNN-title.md#anchor``)."""
    errors = []
    for target, name, anchor in INBOUND_ADR_LINK_RE.findall(BACKTICK_RE.sub("", text)):
        path = adr_dir / name
        if not path.is_file():
            errors.append(f"link to {name}, which does not exist")
        elif (page.parent / target).resolve() != path.resolve():
            errors.append(f"link {target} does not resolve from {page.name}")
        elif anchor not in _file_anchors(path):
            errors.append(f"anchor #{anchor} does not exist in {name}")
    return errors


def validate_index(text: str, path: Path, repo_root: Path, adrs: dict[str, str]) -> list[str]:
    """Return every rule violation in the ADR index; ``adrs`` maps file name -> text."""
    errors = document_errors(text, path, repo_root)
    tables = [table for table in _tables(_section(text, "Index")) if table[0] == INDEX_COLUMNS]
    if not tables:
        return [*errors, f"index needs a table with columns {list(INDEX_COLUMNS)}"]
    listed: Counter[str] = Counter()
    for row in (row for _, rows in tables for row in rows):
        if len(row) != len(INDEX_COLUMNS):
            errors.append(f"index row {row[0]!r}: expected {len(INDEX_COLUMNS)} cells")
            continue
        link, title, status, recorded, issue = row
        match = re.fullmatch(r"\[(\d{4})\]\(((\d{4})-[^)]+\.md)\)", link)
        if match is None or match.group(1) != match.group(3):
            errors.append(f"index row {link!r}: first cell must be [NNNN](NNNN-title.md)")
            continue
        name = match.group(2)
        listed[name] += 1
        if name not in adrs:
            errors.append(f"index links {name}, which does not exist")
            continue
        adr = adrs[name]
        if title != adr_title(adr):
            errors.append(f"index title {title!r} differs from the {name} heading")
        if status != adr_status(adr):
            errors.append(f"index status {status!r} differs from the {name} status")
        if recorded != adr_recorded(adr):
            errors.append(f"index date {recorded!r} differs from the {name} Recorded date")
        reference = re.fullmatch(r"(?:DE-\d{2} )?\[#(\d+)\]", issue)
        if reference is None:
            errors.append(f"index issue {issue!r} must be 'DE-k [#n]' or '[#n]'")
        elif f"[#{reference.group(1)}]" not in adr:
            errors.append(f"{name} does not cite its issue [#{reference.group(1)}]")
    for name in sorted(adrs):
        if listed[name] == 0:
            errors.append(f"{name} is not listed in the index")
        elif listed[name] > 1:
            errors.append(f"{name} is listed {listed[name]} times")
    return errors


# --- The repository's ADRs ------------------------------------------------------


def _adr_paths() -> list[Path]:
    return sorted(path for path in ADR_DIR.glob("*.md") if path.name != INDEX.name)


@pytest.fixture(scope="module")
def register_text() -> str:
    return REGISTER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def adrs() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in _adr_paths()}


def test_adr_folder_holds_only_numbered_records_and_the_index() -> None:
    paths = _adr_paths()
    assert paths, "docs/adr/ needs at least one ADR"
    assert [path.name for path in paths if ADR_FILE_RE.match(path.name) is None] == []
    numbers = [path.name[:4] for path in paths]
    assert len(numbers) == len(set(numbers)), "ADR numbers must be unique"
    assert {path.name for path in ADR_DIR.iterdir()} == {INDEX.name, *(path.name for path in paths)}


@pytest.mark.parametrize("path", _adr_paths(), ids=lambda path: path.stem)
def test_every_adr_is_valid(path: Path, register_text: str) -> None:
    register_ids = frozenset(register_follow_ons(register_text))
    text = path.read_text(encoding="utf-8")
    assert validate_adr(text, path, REPO_ROOT, register_ids=register_ids) == []


def test_index_lists_every_adr_exactly_once(adrs: dict[str, str]) -> None:
    assert validate_index(INDEX.read_text(encoding="utf-8"), INDEX, REPO_ROOT, adrs) == []


def test_each_adr_is_paired_with_the_register_rows_it_answers(
    adrs: dict[str, str], register_text: str
) -> None:
    index = INDEX.read_text(encoding="utf-8")
    checked = 0
    for _, rows in _tables(_section(index, "Index")):
        for row in rows:
            issue = re.fullmatch(r"DE-\d{2} \[#\d+\]", row[-1])
            name = re.search(r"\((\d{4}-[^)]+\.md)\)", row[0])
            if issue is None or name is None:
                continue
            adr = name.group(1)
            assert traceability_errors(adrs[adr], adr, issue.group(0), register_text) == [], adr
            checked += 1
    assert checked, "no index row names a DE issue"


def test_adr_pages_are_in_the_sidebar_and_linked_from_the_architecture() -> None:
    sidebars = SIDEBARS.read_text(encoding="utf-8")
    assert sidebars.count("'adr/README'") == 1
    for path in _adr_paths():
        assert sidebars.count(f"'adr/{path.stem}'") == 1, path.name
    adr_0001 = "../adr/0001-gcp-architecture-and-service-selection.md"
    for doc in ("gcp-bigquery-first.md", "platform-architecture.md", "query-routing.md"):
        assert adr_0001 in (REPO_ROOT / "docs/architecture" / doc).read_text(encoding="utf-8"), doc


def _pages_linking_adrs() -> list[Path]:
    """The README and every docs page outside docs/adr/ and the site build."""
    pages = [
        page
        for page in sorted((REPO_ROOT / "docs").glob("*/*.md*"))
        if page.suffix in {".md", ".mdx"} and page.parent.name not in {"adr", "site"}
    ]
    return [REPO_ROOT / "README.md", *pages]


def test_links_into_adr_headings_resolve() -> None:
    checked = 0
    for page in _pages_linking_adrs():
        text = page.read_text(encoding="utf-8")
        checked += len(INBOUND_ADR_LINK_RE.findall(text))
        assert inbound_adr_anchor_errors(text, page, ADR_DIR) == [], page.relative_to(REPO_ROOT)
    assert checked, "no page links an ADR heading"


def test_adr_0001_records_the_user_decisions_and_non_claims(adrs: dict[str, str]) -> None:
    text = adrs["0001-gcp-architecture-and-service-selection.md"]
    decisions = _section(text, "Decisions")
    location = decisions[decisions.index("### D3 ") : decisions.index("### D4 ")]
    decision = location[location.index("**Decision**:") : location.index("**Options considered**:")]
    assert "US multi-region" in decision
    # Decision, not evidence: it deploys nothing and the lookup target is no SLA.
    assert "deploys nothing" in text
    assert "not a Wiley SLA" in text
    assert "personal projects" in _section(text, "Context")
    cost = _section(text, "Cost model")
    assert "No realistic estimate is possible" in cost
    assert "DE-05 [#126]" in cost and "DE-18 [#139]" in cost
    roles = {
        row[0]
        for header, rows in _tables(_section(text, "Ownership and sign-off"))
        if header[0] == "Role"
        for row in rows
    }
    assert {
        "Lead architect",
        "Cloud platform owner",
        "Security and governance owner",
        "Data engineering owner",
        "API product owner",
    } <= roles
    layers = {
        row[0]
        for header, rows in _tables(_section(text, "Technology selection scorecard (lead Section 24)"))
        if header[:2] == ("Layer", "Criterion")
        for row in rows
    }
    assert layers == set(LEAD_SECTION_24)


# --- Negative cases: the validators themselves must reject bad ADRs. ---

_VALID_ADR = f"""---
id: 0001-example-decision
description: "Example."
---

# ADR 0001: Example decision

## Status

- **Status:** Proposed.
- **Acceptance:** the lead architect approves the pull request for [#124].
- **Recorded:** 2026-10-14.

## Context

Problem for DE-03 [#124]. See the [index](README.md#index) and [cost](#cost-model).

## Decisions

### D1 Control store

**Decision**: Use the control store in the US multi-region.

**Options considered**: a control store; object storage.

**Rationale**: It fits register row L01-01.

**Consequences**: DE-12 adds tests.

**Not selected**: object storage (revisit if the contract shrinks).

**Implemented by**: DE-12 [#133].

**Evidence**: [`example.py`](../../src/example.py).

## Ownership and sign-off

| Role | Accountable for | Inputs owed |
|---|---|---|
| Lead architect | Approves | None |

## Cost model

No prices are used. Cost is instance-hours plus storage.

## Not selected

| Technology or option | Decision | Revisit trigger | Owner DE issue |
|---|---|---|---|
| Object storage as control state | D1 | The control contract shrinks to single-key state | DE-12 [#133] |

## Lead requirement traceability

| Decision | Lead sections answered |
|---|---|
| D1 | §18 |

| Row | Requirement (short) | Answered by | Still open, owned by |
|---|---|---|---|
| L01-01 | Define ingestion | D1 | DE-12 |

## Technology selection scorecard (lead Section 24)

Not applicable: this ADR compares no candidates.

## Consequences and follow-on work

- DE-12 [#133] builds it.

## Rollback

Close the pull request unmerged.

## References

- [#124], [#133].

[#124]: {REPO_URL}/issues/124
[#133]: {REPO_URL}/issues/133
"""

_VALID_INDEX = f"""# Architecture decision records

## Index

| ADR | Title | Status | Recorded | Issue |
|---|---|---|---|---|
| [0001](0001-example-decision.md) | Example decision | Proposed | 2026-10-14 | DE-03 [#124] |

[#124]: {REPO_URL}/issues/124
"""

_OPTIONS = "**Options considered**: a control store; object storage."
_RATIONALE = "**Rationale**: It fits register row L01-01."
_TRIGGER = "The control contract shrinks to single-key state"
_EVIDENCE = "**Evidence**: [`example.py`](../../src/example.py)."
_NOT_APPLICABLE = "Not applicable: this ADR compares no candidates."
_SCORECARD_HEADER = "| Layer | Criterion | Evidence | Alternatives | Measured by |\n|---|---|---|---|---|\n"
_SCORECARD_ROW = "| Operational store | {criterion} | not measured | not measured | DE-30 |"


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs/adr").mkdir(parents=True)
    (tmp_path / "docs/adr/README.md").write_text(_VALID_INDEX, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/example.py").write_text("", encoding="utf-8")
    return tmp_path


def _errors(text: str, repo: Path, name: str = "0001-example-decision.md") -> list[str]:
    return validate_adr(text, repo / "docs/adr" / name, repo, register_ids=frozenset({"L01-01"}))


def test_validator_accepts_a_minimal_valid_adr(fake_repo: Path) -> None:
    assert _errors(_VALID_ADR, fake_repo) == []


def test_validator_accepts_the_superseded_and_scorecard_forms(fake_repo: Path) -> None:
    (fake_repo / "docs/adr/0002-later.md").write_text("", encoding="utf-8")
    superseded = _VALID_ADR.replace("**Status:** Proposed.", "**Status:** Superseded by ADR 0002.")
    assert _errors(superseded, fake_repo) == []
    rows = "\n".join(_SCORECARD_ROW.format(criterion=c) for c in LEAD_SECTION_24["Operational store"])
    scored = _VALID_ADR.replace(_NOT_APPLICABLE, _SCORECARD_HEADER + rows)
    assert _errors(scored, fake_repo) == []


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("id: 0001-example-decision", "id: example-decision", "front matter id must be"),
        ("# ADR 0001: Example decision", "# ADR 0002: Example decision", "needs exactly one H1"),
        ("## Rollback\n", "## Undo\n", "H2 sections must be"),
        ("## Context", "## Background", "H2 sections must be"),
        ("**Status:** Proposed.", "**Status:** Approved.", "unknown status 'Approved'"),
        ("**Status:** Proposed.", "**Status:** Superseded.", "Superseded needs 'by ADR NNNN'"),
        ("**Status:** Proposed.", "**Status:** Superseded by ADR 0009.", "ADR 0009 does not exist"),
        ("**Status:** Proposed.", "**Status:** Deprecated.", "Deprecated needs a reason"),
        ("- **Status:** Proposed.\n", "", "exactly one '**Status:**' line"),
        ("the lead architect approves", "the owner approves", "lead architect's acceptance rule"),
        ("- **Recorded:** 2026-10-14.\n", "", "Recorded:** YYYY-MM-DD"),
        ("### D1 Control store", "### D2 Control store", "numbered D1..D1"),
        ("**Rationale**:", "**Why**:", "D1: missing label **Rationale**:"),
        ("**Rationale**:", "**Rationale:**", "D1: missing label **Rationale**:"),
        ("**Consequences**: DE-12 adds tests.", "**Consequences**:", "label **Consequences**: is empty"),
        (f"{_OPTIONS}\n\n{_RATIONALE}", f"{_RATIONALE}\n\n{_OPTIONS}", "labels must follow the order"),
        (_EVIDENCE, "**Evidence**: trust us.", "Evidence needs"),
        ("**Implemented by**: DE-12 [#133].", "**Implemented by**: the team.", "names no DE issue"),
        ("**Implemented by**: DE-12 [#133].", "**Implemented by**: DE-12.", "in Implemented by needs"),
        (f"| D1 | {_TRIGGER} |", "| D1 | — |", "empty revisit trigger"),
        (f"| D1 | {_TRIGGER} |", "| D1 | None |", "empty revisit trigger"),
        ("state | DE-12 [#133] |", "state | DE-12 |", "owner must be 'DE-k [#n]'"),
        ("state | DE-12 [#133] |", "state | DE-12 [#134] |", "DE-12 is #133, not #134"),
        ("control state | D1 |", "control state | D9 |", "D9 is not a decision"),
        ("control state | D1 |", "control state | — |", "names no Dn"),
        ("control state | D1 |", "control state | D1, D9 |", "D9 is not a decision"),
        ("| D1 | §18 |", "| — | §18 |", "D1 is not in the traceability table"),
        ("register row L01-01.", "register row L99-01.", "register row L99-01 does not exist"),
        ("DE-12 adds tests.", "DE-99 adds tests.", "DE-99 is outside DE-01..DE-35"),
        ("DE-12 adds tests.", "DE-12 [#124] adds tests.", "DE-12 is #133, not #124"),
        ("Problem for DE-03 [#124].", "Problem for [#125] (DE-03).", "DE-03 is #124, not #125"),
        ("DE-12 adds tests.", "DE-12 adds tests [#7].", "[#7] is used but has no link definition"),
        (f"{REPO_URL}/issues/133", f"{REPO_URL}/issues/134", "reference [#133] points at"),
        (f"{REPO_URL}/issues/133", f"{REPO_URL}/pull/133", "DE-12 [#133] must link to /issues/"),
        ("(../../src/example.py)", "(../../src/missing.py)", "link ../../src/missing.py does not resolve"),
        ("(../../src/example.py)", "(../../../outside.py)", "link ../../../outside.py does not resolve"),
        ("[cost](#cost-model)", "[cost](#costs)", "anchor #costs does not exist in this page"),
        ("(README.md#index)", "(README.md#nope)", "anchor #nope does not exist in README.md"),
        ("Problem for", "Ask owner@example.com about", "an email address"),
        ("Problem for", "Data lives in gs://example-bucket for", "a gs:// bucket URL"),
        ("Problem for", "Runs as ci@example.iam.gserviceaccount.com for", "a service-account identity"),
        ("Problem for", "Billed to projects/example-project-1 for", "a projects/<id> project ID"),
        ("Problem for", "Grant serviceAccount:publisher@example-proj for", "an IAM member"),
        ("Problem for", "Grant group:data-eng@example for", "an IAM member"),
        ("Problem for", "Run i118-20261013t101500 showed for", "an untracked run ID"),
        ("Cost is instance-hours", "Cost is 6.25 per TiB and", "unit price '6.25 per TiB'"),
        ("Cost is instance-hours", "Cost is 0.02 per GB-month and", "unit price '0.02 per GB'"),
        ("Cost is instance-hours", "Cost is 1.20/hour and", "unit price '1.20/hour'"),
        ("Cost is instance-hours", "Cost is about $1,200 a month and", "currency amount '$1'"),
        ("Cost is instance-hours", "Cost is 300 USD a month and", "currency amount '300 USD'"),
        ("Cost is instance-hours", "Cost is quoted in dollars and", "Cost model contains currency"),
        ("No prices are used. ", "", "Cost model must say 'No prices'"),
        ("Problem for", "The API is deployed to PROD for", "claims a deployment"),
        ("Problem for", "The control store is live in DEV for", "claims a deployment"),
        ("Problem for", "The service is production-ready for", "claims production readiness"),
        ("Problem for", "Evidence: ENTERPRISE_QA_VERIFIED for", "claims an unproven evidence label"),
        ("Problem for", "Lookups were verified in an enterprise project for", "claims enterprise evidence"),
        ("Problem for", "BigQuery + cache meets the lookup SLA for", "claims an SLA"),
        ("Problem for", "The 200 ms p95 SLA holds for", "claims an SLA"),
        ("| Lead architect | Approves |", "| Platform | Approves |", "with the Lead architect"),
        (_NOT_APPLICABLE, "Scores to follow.", "Layer/Criterion table"),
    ],
)
def test_validator_rejects_rule_violations(fake_repo: Path, old: str, new: str, expected: str) -> None:
    assert _VALID_ADR.count(old) == 1, old
    errors = _errors(_VALID_ADR.replace(old, new), fake_repo)
    assert any(expected in error for error in errors), errors


def test_validator_allows_tracked_run_ids_and_non_price_rates(fake_repo: Path) -> None:
    (fake_repo / "docs/architecture").mkdir()
    (fake_repo / "docs/architecture/runs.md").write_text("Run `i103-20261012t180931`.\n", encoding="utf-8")
    text = _VALID_ADR.replace(
        "Cost is instance-hours",
        "Run i103-20261012t180931 billed at least 10 MiB per table at 5 rps; cost is instance-hours",
    )
    assert _errors(text, fake_repo) == []


def test_inbound_link_validator_rejects_missing_anchors(fake_repo: Path) -> None:
    adr_dir = fake_repo / "docs/adr"
    (adr_dir / "0001-example-decision.md").write_text(_VALID_ADR, encoding="utf-8")
    page = fake_repo / "docs/architecture/page.md"
    good = "See [D1](../adr/0001-example-decision.md#d1-control-store)."
    assert inbound_adr_anchor_errors(good, page, adr_dir) == []
    renamed = good.replace("#d1-control-store", "#d1-renamed")
    assert inbound_adr_anchor_errors(renamed, page, adr_dir) == [
        "anchor #d1-renamed does not exist in 0001-example-decision.md"
    ]
    wrong_dir = "[D1](adr/0001-example-decision.md#d1-control-store)"
    assert inbound_adr_anchor_errors(wrong_dir, page, adr_dir) == [
        "link adr/0001-example-decision.md does not resolve from page.md"
    ]
    readme = "[D1](docs/adr/0001-example-decision.md#d1-control-store)"
    assert inbound_adr_anchor_errors(readme, fake_repo / "README.md", adr_dir) == []
    missing = "[D1](docs/adr/0002-missing.md#d1-control-store)"
    assert inbound_adr_anchor_errors(missing, page, adr_dir) == [
        "link to 0002-missing.md, which does not exist"
    ]


def test_validator_rejects_bad_file_names(fake_repo: Path) -> None:
    errors = _errors(_VALID_ADR, fake_repo, name="1-Example.md")
    assert "1-Example.md: file name must be NNNN-kebab-title.md" in errors


def test_validator_rejects_an_incomplete_scorecard(fake_repo: Path) -> None:
    rows = [_SCORECARD_ROW.format(criterion=c) for c in LEAD_SECTION_24["Operational store"][1:]]
    rows += [
        _SCORECARD_ROW.format(criterion="concurrency"),
        "| Operational store | vibes | not measured | not measured | DE-30 |",
        "| Operational store | cost | not measured | not measured | later |",
    ]
    rows.remove(_SCORECARD_ROW.format(criterion="cost"))
    text = _VALID_ADR.replace(_NOT_APPLICABLE, _SCORECARD_HEADER + "\n".join(rows))
    errors = _errors(text, fake_repo)
    assert "scorecard misses Operational store criterion 'point lookup latency'" in errors
    assert "scorecard row (Operational store, vibes) is not a lead §24 criterion" in errors
    assert "scorecard row ('Operational store', 'concurrency') appears 2 times" in errors
    assert "scorecard row (Operational store, cost): 'Measured by' names no DE issue" in errors


def test_validator_rejects_a_decision_missing_from_the_not_selected_table(fake_repo: Path) -> None:
    second = (
        "### D2 Second\n\n**Decision**: Another.\n\n**Options considered**: two.\n\n"
        "**Rationale**: Because.\n\n**Consequences**: Some.\n\n**Not selected**: the other one "
        "(revisit when asked).\n\n**Implemented by**: DE-12 [#133].\n\n"
        "**Evidence**: not yet measured.\n\n"
        "## Ownership and sign-off"
    )
    text = _VALID_ADR.replace("## Ownership and sign-off", second, 1)
    text = text.replace("| D1 | §18 |", "| D1, D2 | §18 |")
    assert _errors(text, fake_repo) == ["D2 has no row in the Not selected table"]


def test_index_validator_rejects_bad_indexes(fake_repo: Path) -> None:
    adrs = {"0001-example-decision.md": _VALID_ADR, "0002-other.md": _VALID_ADR}
    index = fake_repo / "docs/adr/README.md"
    for name in adrs:
        (fake_repo / "docs/adr" / name).write_text(adrs[name], encoding="utf-8")
    row = next(line for line in _VALID_INDEX.splitlines() if line.startswith("| [0001]"))
    errors = validate_index(_VALID_INDEX, index, fake_repo, adrs)
    assert errors == ["0002-other.md is not listed in the index"]
    doubled = _VALID_INDEX.replace(row, row + "\n" + row)
    errors = validate_index(doubled, index, fake_repo, adrs)
    assert "0001-example-decision.md is listed 2 times" in errors
    for old, new, expected in [
        ("| Proposed |", "| Accepted |", "index status 'Accepted' differs"),
        ("| Example decision |", "| Example |", "index title 'Example' differs"),
        ("| 2026-10-14 |", "| 2026-10-15 |", "index date '2026-10-15' differs"),
        ("[0001](0001-example-decision.md)", "[0003](0003-missing.md)", "index links 0003-missing.md"),
        ("[0001](0001-example-decision.md)", "[0002](0001-example-decision.md)", "first cell must be"),
        ("| DE-03 [#124] |", "| DE-03 |", "must be 'DE-k [#n]' or '[#n]'"),
        ("| DE-03 [#124] |", "| DE-04 [#124] |", "DE-04 is #125, not #124"),
    ]:
        broken = validate_index(_VALID_INDEX.replace(old, new), index, fake_repo, adrs)
        assert any(expected in error for error in broken), (expected, broken)
    assert "index needs a table" in validate_index("# ADRs\n", index, fake_repo, adrs)[0]


def test_heading_anchors_follow_github_slugger() -> None:
    text = (
        "# ADR 0001: GCP architecture\n## Write / transaction boundary\n"
        "### #118 BigQuery + cache measurement\n### §3 Canonical Data Layer\n"
        "## Technology selection scorecard (lead Section 24)\n## Status\n## Status\n"
        "```\n## not a heading\n```\n### D4 Durable control plane: Cloud SQL, control only\n"
    )
    assert heading_anchors(text) == {
        "adr-0001-gcp-architecture",
        "write--transaction-boundary",
        "118-bigquery--cache-measurement",
        "3-canonical-data-layer",
        "technology-selection-scorecard-lead-section-24",
        "status",
        "status-1",
        "d4-durable-control-plane-cloud-sql-control-only",
    }


_TRACE_REGISTER = """\
| ID | Sec | Requirement | Paths | Follow-on |
|---|---|---|---|---|
| L01-01 | 1 | First | `docs/adr/0001-example-decision.md`; `README.md` | DE-07 [#128] |
| L01-02 | 1 | Second | `README.md` | DE-03 [#124]; DE-08 [#129] |
| L01-03 | 1 | Third | `README.md` | DE-09 [#130] |
"""

_TRACE_ADR = """\
## Lead requirement traceability

| Decision | Lead sections answered |
|---|---|
| D1 | §1 |

| Row | Requirement (short) | Answered by | Still open, owned by |
|---|---|---|---|
| L01-01, L01-02 | First and second | D1 | DE-07, DE-08 |

## Technology selection scorecard (lead Section 24)
"""


def _trace(adr: str = _TRACE_ADR, register: str = _TRACE_REGISTER) -> list[str]:
    return traceability_errors(adr, "0001-example-decision.md", "DE-03 [#124]", register)


def test_traceability_validator_accepts_paired_rows() -> None:
    assert _trace() == []


@pytest.mark.parametrize(
    ("page", "old", "new", "expected"),
    [
        (
            "register",
            "| L01-03 | 1 | Third | `README.md` |",
            "| L01-03 | 1 | Third | `docs/adr/0001-example-decision.md` |",
            "L01-03 cites or names this ADR but is not in its register-row table",
        ),
        (
            "register",
            "| L01-02 | 1 | Second | `README.md` | DE-03 [#124]; DE-08 [#129] |",
            "| L01-02 | 1 | Second | `README.md` | DE-08 [#129] |",
            "L01-02 neither cites docs/adr/0001-example-decision.md nor names DE-03 [#124]",
        ),
        (
            "register",
            "| DE-07 [#128] |",
            "| DE-07 [#128]; DE-10 [#131] |",
            "L01-01 follows ['DE-10'] not named as still open",
        ),
        (
            "adr",
            "| DE-07, DE-08 |",
            "| DE-07, DE-08, DE-35 |",
            "L01-01, L01-02: still open names ['DE-35'] no row follows",
        ),
        ("adr", "| L01-01, L01-02 |", "| L01-01, L01-02, L09-99 |", "L09-99 is not a register row"),
        (
            "adr",
            "| Still open, owned by |",
            "| Open work |",
            "the register-row table has no 'Still open' column",
        ),
    ],
)
def test_traceability_validator_rejects_drift(page: str, old: str, new: str, expected: str) -> None:
    adr, register = _TRACE_ADR, _TRACE_REGISTER
    if page == "adr":
        assert old in adr
        adr = adr.replace(old, new)
    else:
        assert old in register
        register = register.replace(old, new)
    assert expected in _trace(adr, register)
