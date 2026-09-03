# AGENTS.md — `docs/`: the documentation-authoring contract

Canonical, vendor-neutral instructions for anyone — human or agent — adding
to or editing `docs/`, mirroring the repo-root `AGENTS.md`/`CLAUDE.md` split
(root `CLAUDE.md` "M1-1"): this file is the source of truth, `docs/CLAUDE.md`
is a thin adapter that imports it via `@AGENTS.md`. If you're changing a
documentation rule, edit **this file**, not the adapter.

Published via `mkdocs` (config in `/mkdocs.yml`). CI runs `mkdocs build
--strict`, so dangling internal links fail the build.

## The governing rule

> One fact is defined in exactly one place. One question is explained in
> full on exactly one page. Everywhere else, only a short, adapted summary
> that links back to the canonical source is allowed.

Four different things can "own" a piece of documentation content, and
conflating them is the single most common way this repo's docs have drifted
out of sync with each other and with the code:

| Ownership kind | What it means | Example |
|---|---|---|
| **Fact owner** | Where the exact, machine-checked value lives | `ChangeKind` registry, CLI `help=` text, JSON Schema, `examples/ground_truth.json` |
| **Narrative owner** | Where a topic is explained in full, for a human | `learn/evidence-and-detectability.md` |
| **Task owner** | Where one practical user workflow is described | `use/scan-levels.md` |
| **View owner** | Where a fact is shown again in a different shape | a generated reference table, a one-paragraph quickstart summary, a case-index row |

For evidence, this split is already real (and predates this file):
`scripts/evidence_tiers.py` owns the exact per-`ChangeKind` minimum-evidence
values; `learn/evidence-and-detectability.md` owns the full mental model
(including the `--depth` dial and the deprecated-axes appendix);
`learn/what-each-level-sees.md` owns one worked example walking the same
model level-by-level; `use/scan-levels.md` owns the practical `--depth`
choice. `getting-started.md`, `start/first-check.md`, and
`start/choose-your-workflow.md` may each carry a short summary that links
back — not a second explanation.

`docs/_meta/topics.yaml` makes this split machine-checkable instead of a
convention only documented in prose (see "Topic ownership registry" below).

## Layout

`docs/` is organized into five top-level trees plus one machine-only
directory (ADR-051 Stage 4). Note: file locations and `mkdocs.yml` nav
grouping are independent — a few files are grouped in the nav differently
than their directory would suggest; keep links pointing at the real file
path, not the nav position.

- `index.md` — home / landing page.
- `start/` — first-contact onboarding: `getting-started.md` is a short hub
  (navigated as the first page of the User Guide) linking to `install.md`,
  `first-check.md`, and `first-report.md` — one question each (install, run
  a check, read the result) — plus the worked real-world example
  (`start/real-world-example.md`). New user, first five minutes.
- `learn/` — physically one directory, but split across two *nav* tracks by
  what kind of question a page answers (docs/AGENTS.md's own "Fact owner /
  Narrative owner / Task owner / View owner" split, applied at the
  tab level):
  - The **educational track** — general ABI/API compatibility knowledge that
    holds regardless of which tool you use — is navigated under the
    **ABI/API Compatibility** tab as **numbered steps, and the sidebar
    is the reading order**: one nav group per step of
    `docs/_meta/learning-ladder.yaml`, titled `<n>. <title>`, in step
    order, holding that step's pages in ladder order (the hub sits alone
    directly under the tab). `scripts/learning_ladder.py`'s sidebar rule,
    run by `scripts/check_docs_contract.py`, fails when the two drift, so
    the sidebar, the hub's step list, and every page's `**Ladder:**` footer
    tell one order. The steps: **1. Start Here** (five-minute on-ramp, how
    a break shows up, cheat sheet, glossary) → **2. Foundations** (Parts
    0-1, your ABI surface) → **3. How Breaks Happen** (Parts 2-6, with the
    advanced "go deeper" branches — `class-layout-abi.md`,
    `exception-unwinding-abi.md`, `modern-cpp-toolchain-hazards.md`,
    `msvc-pe-abi-model.md` — as a nested *Go Deeper (optional)* group at
    the end of the step) → **4. Designing for Stability** (Part 7, so the
    numbered Parts read straight through Steps 2-4) → **5. Define Your
    Contract** (direction, consumer models, build-profile comparability,
    static/header-only) → **6. Detect Breaks** (`abi-series/08-detection.md`
    + `assurance-methods.md`; from here the subject is catching breaks) → **7. In Practice**
    (where in the pipeline, surface growth, rollout and governance, triage)
    → **8. At Scale** (products, template-heavy libraries, system-library
    discipline, runtime floors, environment drift, packages and consumers)
    → **9. Beyond Static ABI** (behavioral, data/wire, ownership,
    concurrency). Each Part's own "Series navigation" breadcrumb (top of
    `abi-series/00-*.md` … `07-*.md`; `08-detection.md` is Step 6's first
    page, not a Part, and carries none) lists exactly Parts 0-7, the
    numbered spine inside those steps. To move a page or a step, edit the ladder file and
    `mkdocs.yml` together, then regenerate the hub
    (`python scripts/gen_learning_ladder.py`) and rewrite the affected
    footers. An earlier layout grouped this tab by *question asked*
    independently of the ladder, which left the sidebar, the hub and the
    footers each telling a different order; that is what the rule retires.
  - The **tool track** — how abicheck itself models and reports compatibility
    — is navigated under the **Concepts** tab: verdicts,
    `evidence-and-detectability.md`, `architecture.md`, build/source data,
    graph coverage, impact assessment,
    `elf-symbol-filtering.md` (a specific abicheck scan-mode behavior, not
    general ABI knowledge — kept out of the educational tab even though its
    file lives in the same directory), and `limitations.md`.

  A page whose job is genuinely both — mapping a general ABI concept to the
  exact `ChangeKind`s/evidence tiers abicheck emits for it, e.g.
  `class-layout-abi.md`, `msvc-pe-abi-model.md` — stays on the educational
  tab (that mapping *is* the pedagogical content), but keeps abicheck's own
  internal module/function names out of its prose and headings; name them in
  the page's own `depends_on` front matter instead, which exists precisely
  for that traceability without cluttering the reader-facing explanation.
  Every page under the **ABI/API Compatibility** tab has been swept to this
  rule (including `build-profile-comparability.md`, the previous holdout) —
  a new internal-name leak here is a real regression to fix, not an
  unmigrated pre-existing page to tolerate. `ChangeKind`/`RecordType`/
  `AbiSnapshot` and public JSON-report field values (e.g. `dwarf_aware`) are
  documented public vocabulary, not internal names, and stay.

  The evidence model is deliberately a three-page trio with one role each —
  model (`learn/evidence-and-detectability.md`), worked example
  (`learn/what-each-level-sees.md`), and flag reference
  (`use/scan-levels.md`) — don't add a fourth page to that topic. Verdict
  semantics live on one page (`learn/verdicts.md`, including the
  verdict→exit-code chain); `reference/exit-codes.md` stays the exhaustive
  per-command authority.
- `use/` — task-oriented how-to docs (GitHub Action, CLI flags, policy
  files, suppression, output formats, `troubleshooting.md`). Nav is grouped
  basics-first: Start Here → Everyday Use → CI & Gating → Specialised
  Checks → Integrations & Migration; `troubleshooting.md` is navigated
  under **Everyday Use**, alongside the other day-to-day pages.
- `reference/` — exhaustive, looked-up-not-read-linearly material:
  - curated reference (change kinds, exit codes, platforms, tool comparison,
    ABICC format compliance) at the top level, navigated as its own
    **Reference** tab. `reference/change-kinds.md` is the *curated,
    narrative* change-kind guide; `reference/detector-spec.md` is the
    *exhaustive, generated* matrix (every `ChangeKind` × category × verdict
    × severity × minimum evidence). Don't duplicate rows from one into the
    other — link instead.
  - `reference/examples/` — per-case Markdown docs that match the binary
    fixtures in `/examples/`. Generated via `scripts/gen_examples_docs.py`
    — regenerate after adding a new example. Navigated as its own
    **Examples** tab (index + by-verdict + by-category; per-case pages are
    linked, not in nav).
  - `reference/schemas/` — the published, versioned JSON Schema mirror
    (`scripts/publish_schemas.py` keeps it synced with the package's schemas).
- `contribute/` — contributor- and governance-facing docs: architecture,
  parity status, goals, plans, the archive, the use-case registry
  (`contribute/usecase-registry.yaml`), and ADRs in `contribute/adr/`.
- `_meta/` — machine-consumed registries only (`topics.yaml`, topic
  ownership; `terminology.yaml`, per-term definition ownership). Not
  published as site pages: mkdocs only builds `*.md` files, and this
  directory intentionally contains none. Don't add a `README.md` here
  without also excluding it from the nav-coverage check the way
  `docs/CLAUDE.md` is excluded (`exclude_docs` in `mkdocs.yml`).

Every pre-Stage-4 URL still resolves: `mkdocs.yml`'s `redirect_maps` (via the
`mkdocs-redirects` plugin) carries a 30x redirect for each moved, rendered
page. Don't remove a `redirect_maps` entry without checking whether the old
URL is still externally linked (e.g. from `CHANGELOG.md` history, an
external blog post, or a GitHub issue) — the entries are cheap to keep and
expensive to silently break.

## Conventions

- Every page must be reachable from `mkdocs.yml` nav (mkdocs --strict
  enforces this). Exceptions: per-case `examples/*.md` pages are linked from
  the encyclopedia indexes instead of the nav, and this `AGENTS.md`/
  `CLAUDE.md` are excluded from the published site via `exclude_docs`.
- The docs tell a two-track story: an **educational track** (ABI/API
  Compatibility tab — understanding the problem) and a **tool track** (User
  Guide → Concepts → Reference — using and understanding abicheck). Within
  each track, order pages simple → advanced.
- Use relative links (`../use/x.md`), not absolute URLs.
- Prefer pulling from `--help` output rather than hand-rolling CLI
  tables — use the same wording the user sees.
- `ChangeKind` references: use the enum value (e.g. `symbol_removed`)
  or the enum NAME (`SYMBOL_REMOVED`); the AI-readiness check accepts
  either form.
- Don't hand-copy a table, count, or version number that already has a fact
  owner elsewhere (a registry, a schema, `repo_facts.json`) — link to it or
  pull it through the page's existing generator instead. `repo_facts.json`
  (CLAUDE.md "M1-4") is the model to follow for any new volatile fact.

## Topic ownership registry

`docs/_meta/topics.yaml` declares, per topic id, which page is the
`canonical_page` (narrative owner), which pages are its `worked_example`,
`task_pages`, `reference_page`, and `allowed_summaries` (view/task owners
permitted to reference it), and which code/schema paths are its
`fact_sources`. It does not describe topic *content* — only *ownership*.

`scripts/check_docs_contract.py` (wired into `scripts/verify.py --profile pr`
as the `docs-contract` step) enforces, as **hard errors**:

- every path a topic references (`canonical_page`, `worked_example`,
  `reference_page`, each `task_pages`/`allowed_summaries` entry, each
  `fact_sources` entry) actually exists;
- no two topics claim the same `canonical_page`;
- if a `canonical_page` file carries the front-matter schema below and sets
  `canonical_for`, the ids there must round-trip back to that same topic (a
  page can't claim ownership of a topic another page already owns, and a
  topic's registered `canonical_page` can't silently point at a file that
  disclaims ownership);
- a page's `summarizes` entries must round-trip too: the page itself must be
  registered as that topic's `worked_example`, `reference_page`, or a
  `task_pages`/`allowed_summaries` entry — a page can't grant itself
  permission to restate a topic just by adding the front-matter claim;
- a topic's `canonical_page` can't itself be marked `generated: true` — the
  canonical_page is the hand-authored narrative owner by definition, so a
  registry entry pointing it at a generated page is a misconfiguration
  (register the generated page as `reference_page` instead).

**Registration is no longer pilot-only for new work.** The registry started
as a pilot covering a handful of topics that already had an explicit
ownership split documented before this file existed; it has since grown to
20+ registered topics covering most major product surfaces. Every **new**
public-facing feature or surface (a new CLI command/flag family, a new
report field, a new config namespace, a new Action input) must register a
topic in `docs/_meta/topics.yaml` — `canonical_page`, a `reference_page`/
`task_pages` if applicable, and `fact_sources` pointing at its code — in the
same PR that adds the feature, not deferred to a later cleanup. Registering
an *existing*, already-documented page that predates this file remains
incremental and opportunistic, same as the front-matter rollout below.

As **warnings** (non-blocking; the check that flags likely accidental
duplication, not a structural ownership conflict):

- a `canonical_page` without any front matter at all (the schema below is
  being rolled out incrementally — see "Rollout status");
- an identical, long (40+ word) paragraph, or an identical Markdown table
  (much lower floor — 10+ words, since a short copy-pasted reference table
  is exactly this scan's target case), appearing verbatim in two or more
  manual (non-generated) pages — usually a sign one of them should be a
  summary-with-link instead of a second explanation;
- a page other than a `terminology.yaml` term's registered `canonical_page`
  appearing to define that term itself (see "Terminology registry" below);
- a manual, non-generated page (outside `contribute/adr/`, `contribute/plans/`,
  `contribute/archive/`, and pages with `lifecycle: migration`/`historical`
  front matter — all inherently historical/planning records) containing an
  in-progress status phrase such as "being updated in parallel", "currently
  being implemented", "work in progress", or `TBD`/`TODO:` — this class of
  claim is true only until the described work ships, then goes stale with
  nothing else to catch it; a hit needs a human read, not an automatic fix;
- the same class of page naming a retired CLI flag/command/file by its exact
  dead spelling (`scripts/check_docs_contract.py`'s `_RETIRED_SURFACES`
  registry — e.g. `abicheck-mcp`, `mcp_server.py`, `--source-abi-cache`) with
  no allowlist entry for that page — a real-world instance of this was found
  in a documentation review: ADR-058/G36 still described the just-removed
  MCP server as a live, optional execution adapter. Add a registry entry
  whenever a PR deletes a flag/command/file another doc might still name.
  This sweep also reads `examples/case*/README.md`, since those are the
  generator sources for the published `docs/reference/examples/` case pages
  — the generated pages themselves are skipped, so a stale flag left in a
  case README would otherwise reproduce into a public page on the next
  regeneration with nothing to catch it, and over `tests/scenarios/*.yaml`,
  whose `flow:` entries are commands a reader is meant to be able to run --
  the catalogue's structural tests check that a flow *has* an automated
  counterpart, not that the command it prints still parses;
- a documented `abicheck <subcommand> ...` line — or an Action `extra-args`
  value, which is raw argv by another name — passing a `.abicheck.yml` key as
  an operand (`abicheck compare a b severity.addition: error`). This is what a
  mechanical flag-to-config-key rewrite produces when it reaches an *example*
  rather than the prose naming the key: correct-looking, and exit 64 for
  anyone who copies it. Prose and YAML config blocks are deliberately
  untouched, since the same token is the right spelling there;

## Terminology registry

`docs/_meta/terminology.yaml` is `topics.yaml`'s counterpart for individual
terms rather than whole topics: each entry names the one page responsible
for defining a term (`canonical_page`) and a one-sentence `short_definition`.
Unlike a topic's `canonical_page`, a term's `canonical_page` need not be
unique — two terms (e.g. ABI and API) may legitimately share one defining
page. `check_docs_contract.py` enforces, as **errors**, that every entry has
a `canonical_page` that exists and a `short_definition`; as a **warning**, it
flags any other page that appears to *define* a registered term itself (a
bolded term immediately followed by a definition connector — "is", "means",
"—", etc. — not just a mention or a link) instead of linking back to the
term's `canonical_page`. This is deliberately narrow: it only catches an
actual re-definition pattern, so ordinary correct usage of a term elsewhere
never triggers it. Add an entry only for a term that already shows up
defined in more than one or two places — a term used on exactly one page
doesn't need one.

## Page front matter

Manual pages *may* carry YAML front matter (mkdocs parses it natively, no
plugin required) describing the page's role:

```yaml
---
doc_type: how-to
audience:
  - library-maintainer
canonical_for:
  - baseline-lifecycle
summarizes:
  - evidence-model
depends_on:
  - abicheck/model/snapshot.py
lifecycle: active
generated: false
---
```

| Field | Meaning |
|---|---|
| `doc_type` | One of `hub`, `tutorial`, `how-to`, `explanation`, `reference`, `case`, `migration`, `contributor`. |
| `audience` | Who the page is written for (free-form list, e.g. `library-maintainer`, `ci-owner`). |
| `level` | `beginner`, `intermediate`, `advanced`, or `expert`. |
| `canonical_for` | Topic ids (from `topics.yaml`) this page is the narrative owner of. Usually empty or one entry. |
| `summarizes` | Topic ids this page briefly references without owning — the page must link to that topic's `canonical_page` rather than re-explain it. |
| `depends_on` | Repo-relative paths (code, CLI commands, config keys) whose change should prompt a look at this page. `scripts/check_docs_review_triggers.py` (CI: `docs-review-triggers.yml`) diffs this against a PR's changed files and posts an `::notice::` + step-summary table when they overlap — advisory only, it never fails the build (a path-prefix match is a heuristic, not proof the page is actually stale). |
| `lifecycle` | `active`, `migration`, or `historical`. |
| `generated` | `true` for machine-generated pages (don't hand-edit; `check_docs_contract.py` skips front-matter enforcement on these). |

**Rollout status**: front matter is populated today only on the pages
referenced by `docs/_meta/topics.yaml` (the pilot topic set above) — it is
not yet required repo-wide for *existing* pages, which may migrate
incrementally. Extend both files together when you add a new topic to the
registry; don't add front matter to an unrelated page as a drive-by, since
an orphaned `canonical_for`/`summarizes` entry not backed by a
`topics.yaml` topic is exactly the kind of unchecked claim this schema
exists to prevent silently accumulating. Every **newly created** manual
public page must carry front matter from the day it's added, though — at
minimum `doc_type`/`level`/`lifecycle`, plus `canonical_for` if it's the
new canonical owner of a topic you're registering in the same change; a
brand-new page starting without it is exactly the debt this pilot is
meant to stop accumulating, not something to defer to a later cleanup.

**ADRs are exempt from this front-matter schema**, including new ones —
`contribute/adr/*.md` uses its own established metadata convention instead
(a `**Date:**`/`**Status:**`/optional `**Decision maker:**`/optional
`**Verified:**` block right under the title). Of that block, only `Status`
(and, when present, `Verified`) is actually machine-checked —
`scripts/adr_status_sync.py` validates a `Status` line/heading exists and
agrees with `adr/index.md`, and validates a well-formed `Verified` receipt
when one is given; `Date` and `Decision maker` are convention, not gated by
anything today, so an ADR missing a `Date` still passes every existing
check. This was flagged as an open inconsistency in a documentation review
(new ADRs carry no YAML front matter despite the "every newly created
manual public page" rule above) and is resolved here explicitly rather
than left ambiguous: introducing YAML front matter on top of the
already-working `Status`/`Verified` convention would be a second,
redundant metadata schema for information that convention already covers
— it would not, by itself, close the gap that `Date`/`Decision maker` are
unenforced, which is a separate, still-open gap this exemption doesn't
claim to fix. Don't add YAML front matter to an ADR on the strength of the
general rule above.

## When does a new fact need a new page?

Adding a feature does not, by itself, justify a new page. Create one only if
at least one of these holds:

1. a genuinely new, self-contained user workflow appeared;
2. a new mental model/concept is required to use the feature correctly —
   not just another variant of an already-explained mechanism;
3. a large, self-contained reference namespace appeared (e.g. a new schema);
4. a new kind of compatibility contract or a new audience appeared;
5. an existing page has grown to answer more than one primary question and
   splitting it serves readers better than continuing to append to it.

Otherwise, extend the existing canonical owner (found via `topics.yaml` if
the topic is registered, or via the "Layout" table above otherwise) instead
of starting a new file. If you do add a page, and it's the canonical owner
of a topic covered above, register it in `docs/_meta/topics.yaml`.

See [Writing Documentation](contribute/documentation.md) for this contract's
human-readable companion — page-shape templates, worked before/after
duplication fixes, the document lifecycle, and the PR checklist.

## Regenerating generated docs

```bash
python scripts/gen_examples_docs.py       # docs/reference/examples/*.md
python scripts/gen_detector_spec.py       # docs/reference/detector-spec.{md,json}
python scripts/gen_action_reference.py    # docs/reference/github-action-inputs.md
python scripts/gen_cli_reference.py       # docs/reference/cli-reference.md
python scripts/gen_python_api_reference.py  # docs/reference/python-api-reference.md
python scripts/gen_config_reference.py    # docs/reference/config-keys-reference.md
python scripts/gen_platform_matrix.py     # docs/reference/platforms.md's "Quick Reference" section
python scripts/gen_learning_ladder.py     # docs/learn/abi-api-handling.md's numbered step list and role-path table (from docs/_meta/learning-ladder.yaml)
python scripts/gen_backend_capability_matrix.py  # docs/reference/header-backend-capabilities.md's fact matrix
python scripts/gen_fact_capability_matrix.py      # docs/reference/fact-registry.md
python scripts/gen_catalog_coverage_report.py     # docs/contribute/catalog-coverage.md (rule/variant/scenario/ecosystem/workflow coverage)
python scripts/gen_agent_skills.py        # .agents/skills/, .claude/skills/, .gemini/skills/ (from skills-src/)
```

`gen_agent_skills.py` is not a `docs/` generator, but it is under the same
"generated, drift-gated, one canonical source" contract and is listed here so
the regeneration commands live in one place — with one difference from every
other row above: **its output is not committed** (2026-08-21 ADR-058
amendment). `.agents/skills/`, `.claude/skills/`, and `.gemini/skills/` are
gitignored build output, regenerated by CI on demand (`gen_agent_skills.py
--check` validates `skills-src/`'s own internal consistency rather than
diffing against checked-in files) and by `scripts/install_dev_skill.py`
locally when a contributor wants to exercise an installed skill.

Commit the resulting files for every generator above `gen_agent_skills.py`.
`scripts/verify.py --profile pr` (via the `ai-readiness`/`fair-metadata`
steps) fails if a generated file has drifted from its generator.

## Verification

```bash
python scripts/check_docs_contract.py       # this file's rules, standalone
python scripts/verify.py --profile pr --only docs-build,docs-contract
python scripts/verify.py --profile pr        # full PR-equivalent gate
```
