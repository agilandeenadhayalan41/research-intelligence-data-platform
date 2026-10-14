# Architecture decision records

An architecture decision record (ADR) records one decision, or one closely linked set of decisions, that selects a technology, a policy or a boundary for this platform, together with the options considered, the evidence and the conditions that would reopen it. An ADR is a decision, not evidence: accepting one never changes a readiness level, a register status or an evidence label, and never authorizes creating cloud resources or running billed jobs. Requirement-level status stays in the [lead coverage register](../requirements/openalex-lead-coverage.md).

## Index

| ADR | Title | Status | Recorded | Issue |
|---|---|---|---|---|
| [0001](0001-gcp-architecture-and-service-selection.md) | GCP architecture and service selection | Proposed | 2026-10-14 | DE-03 [#124] |

## Numbering and file names

- Files are named `NNNN-kebab-title.md` in this folder: a four-digit number, then a short lowercase title with hyphens (for example `0001-gcp-architecture-and-service-selection.md`).
- Numbers are sequential and never reused, including for withdrawn or superseded ADRs.
- Each ADR starts with front matter `id: NNNN-kebab-title` (without it the docs site strips the number, which Docusaurus treats as an ordering prefix), followed by the heading `# ADR NNNN: Title`. The index above lists every ADR exactly once.

## Statuses

| Status | Meaning | Next status |
|---|---|---|
| Proposed | Written and under review in an open pull request; not binding. | Accepted, or withdrawn by closing the pull request unmerged |
| Accepted | The lead architect approved the pull request and it was merged; binding until superseded or deprecated. | Superseded or Deprecated |
| Superseded | Replaced by a later ADR, named in the status line ("Superseded by ADR NNNN"). | none |
| Deprecated | No longer applies and nothing replaces it; the status line says why. | none |

## Required sections

Every ADR has these H2 sections, in this order. A section that does not apply stays, with one line saying why (for example "Not applicable: this ADR selects no technology").

1. `## Status`: the status, the acceptance rule, the date, the base commit, the sign-off roles, and any decision that is *selected, not locked* or conditional.
2. `## Context`: the problem, the lead sections involved, current evidence with links, and what is not proven.
3. `## Decisions`: one `### Dn <title>` per decision, numbered from D1. Each carries the bold labels **Decision**, **Options considered**, **Rationale**, **Consequences**, **Not selected** (each option with a measurable revisit trigger), **Implemented by** (DE ids with issue links) and **Evidence** (repository links, or "not yet measured").
4. `## Ownership and sign-off`: accountable roles and the inputs each owes. Roles only: no personal names, emails or project IDs.
5. `## Cost model`: per-component formulas and drivers, only numbers actually measured (with run, environment and scale), and what must happen before a realistic estimate exists. No prices.
6. `## Not selected`: one table of technology, decision, revisit trigger and owner DE issue.
7. `## Lead requirement traceability`: which lead sections and register rows each decision answers.
8. `## Technology selection scorecard (lead Section 24)`: per criterion, the candidates, the evidence or "not measured", and the DE issue that measures it.
9. `## Consequences and follow-on work`
10. `## Rollback`: how the decision is reversed or superseded.
11. `## References`

## Writing rules

- Evidence is never upgraded by implication. Say where each number was measured (local, GCP_SANDBOX, personal DEV/QA, enterprise) and at what scale; personal or operator-owned projects are not enterprise evidence.
- General cloud product knowledge may weigh options, but it is labelled as a product characteristic, not repository evidence, and it never includes prices.
- No private identifiers: no project IDs, bucket names, identity names, emails or untracked run IDs. The repository and the docs site are public.
- DE planning IDs are written with their issue: DE-*k* is GitHub issue #(121+*k*), for example DE-04 [#125].
- Links are relative and must resolve on GitHub and on the docs site (code links such as `../../src/...`).

## Sign-off

- An ADR is proposed in a pull request that references its issue.
- **Sign-off is the lead architect's approval of that pull request.** At approval the status line is changed from Proposed to Accepted, with the date, in the same pull request; the approval and the merge commit record acceptance, and no separate signature exists.
- The other accountable roles (Cloud platform, Security and governance, Data engineering, API product owner) are consulted; their open inputs are listed in the ADR. DE-04 [#125] assigns the role holders.
- An accepted ADR is not edited in place except for its status line, links and typos. A changed decision is a new ADR that names the decisions it replaces; in the same pull request the old ADR's status becomes Superseded (or Deprecated).

[#124]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/124
[#125]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/125
