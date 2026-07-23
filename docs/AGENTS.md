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
| **Narrative owner** | Where a topic is explained in full, for a human | `concepts/evidence-and-detectability.md` |
| **Task owner** | Where one practical user workflow is described | `user-guide/scan-levels.md` |
| **View owner** | Where a fact is shown again in a different shape | a generated reference table, a one-paragraph quickstart summary, a case-index row |

For evidence, this split is already real (and predates this file):
`scripts/evidence_tiers.py` owns the exact per-`ChangeKind` minimum-evidence
values; `concepts/evidence-and-detectability.md` owns the full mental model
(including the `--depth` dial and the deprecated-axes appendix);
`concepts/what-each-level-sees.md` owns one worked example walking the same
model level-by-level; `user-guide/scan-levels.md` owns the practical `--depth`
choice. `getting-started.md` and `user-guide/choose-your-workflow.md` may
each carry a short summary that links back — not a second explanation.

`docs/_meta/topics.yaml` makes this split machine-checkable instead of a
convention only documented in prose (see "Topic ownership registry" below).

## Layout

Note: file locations and `mkdocs.yml` nav grouping are independent. Several
files live at the docs root or under `concepts/`/`reference/` but are grouped
elsewhere in the nav — keep links pointing at the real file path.

- `index.md` — home / landing page.
- `getting-started.md` — top-level file, but navigated as the **first page of
  the User Guide**.
- `troubleshooting.md` — top-level file, but navigated under **Development**.
- `user-guide/` — end-user docs (getting started, GitHub Action, CLI flags,
  policy files, suppression, output formats). Nav is grouped basics-first:
  Start Here → Everyday Use → CI & Gating → Specialised Checks →
  Integrations & Migration.
- `concepts/` — conceptual docs (verdicts, evidence model, architecture, and
  `abi-api-handling.md` — the consolidated ABI/API handling guide).
  `abi-cheat-sheet.md`, `abi-api-handling.md`, and the deep-dive pages
  (`abi-surface.md`, `class-layout-abi.md`, `dependency-floors.md`) are
  navigated under the educational **ABI/API Handling & Recommendations** tab
  (the deep-dives under its **Deep Dives** group), not Concepts.
  The evidence model is deliberately a three-page trio with one role each —
  model (`concepts/evidence-and-detectability.md`), worked example
  (`concepts/what-each-level-sees.md`), and flag reference
  (`user-guide/scan-levels.md`) — don't add a fourth page to that topic.
  Verdict semantics live on one page (`concepts/verdicts.md`, including the
  verdict→exit-code chain); `reference/exit-codes.md` stays the exhaustive
  per-command authority.
- `reference/` — curated reference (change kinds, exit codes, platforms, tool
  comparison, ABICC format compliance). Navigated as its own **Reference** tab.
  `reference/change-kinds.md` is the *curated, narrative* change-kind guide;
  `reference/detector-spec.md` is the *exhaustive, generated* matrix (every
  `ChangeKind` × category × verdict × severity × minimum evidence). Don't
  duplicate rows from one into the other — link instead.
- `examples/` — per-case Markdown docs that match the binary fixtures
  in `/examples/`. Generated via `scripts/gen_examples_docs.py` —
  regenerate after adding a new example. Navigated as its own **Examples**
  tab (index + by-verdict + by-category; per-case pages are linked, not
  in nav).
- `development/` — contributor-facing docs (architecture, parity status,
  goals, ADRs in `development/adr/`).
- `_meta/` — machine-consumed registries only (`topics.yaml`, topic
  ownership; `terminology.yaml`, per-term definition ownership). Not
  published as site pages: mkdocs only builds `*.md` files, and this
  directory intentionally contains none. Don't add a `README.md` here
  without also excluding it from the nav-coverage check the way
  `docs/CLAUDE.md` is excluded (`exclude_docs` in `mkdocs.yml`).

## Conventions

- Every page must be reachable from `mkdocs.yml` nav (mkdocs --strict
  enforces this). Exceptions: per-case `examples/*.md` pages are linked from
  the encyclopedia indexes instead of the nav, and this `AGENTS.md`/
  `CLAUDE.md` are excluded from the published site via `exclude_docs`.
- The docs tell a two-track story: an **educational track** (ABI/API Handling
  tab — understanding the problem) and a **tool track** (User Guide → Concepts
  → Reference — using and understanding abicheck). Within each track, order
  pages simple → advanced.
- Use relative links (`../user-guide/x.md`), not absolute URLs.
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
  appearing to define that term itself (see "Terminology registry" below).

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
  - abicheck/model.py
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
| `depends_on` | Repo-relative paths (code, CLI commands, config keys) whose change should prompt a look at this page. Informational today — a review-trigger check that diffs this against a PR's changed files is not yet wired into CI. |
| `lifecycle` | `active`, `migration`, or `historical`. |
| `generated` | `true` for machine-generated pages (don't hand-edit; `check_docs_contract.py` skips front-matter enforcement on these). |

**Rollout status**: front matter is populated today only on the pages
referenced by `docs/_meta/topics.yaml` (the pilot topic set above) — it is
not yet required repo-wide. Extend both files together when you add a new
topic to the registry; don't add front matter to an unrelated page as a
drive-by, since an orphaned `canonical_for`/`summarizes` entry not backed by
a `topics.yaml` topic is exactly the kind of unchecked claim this schema
exists to prevent silently accumulating.

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

See [Writing Documentation](development/documentation.md) for this contract's
human-readable companion — page-shape templates, worked before/after
duplication fixes, the document lifecycle, and the PR checklist.

## Regenerating generated docs

```bash
python scripts/gen_examples_docs.py     # docs/examples/*.md
python scripts/gen_detector_spec.py     # docs/reference/detector-spec.{md,json}
python scripts/gen_action_reference.py  # docs/reference/github-action-inputs.md
```

Commit the resulting files. `scripts/verify.py --profile pr` (via the
`ai-readiness`/`fair-metadata` steps) fails if a generated file has drifted
from its generator.

## Verification

```bash
python scripts/check_docs_contract.py       # this file's rules, standalone
python scripts/verify.py --profile pr --only docs-build,docs-contract
python scripts/verify.py --profile pr        # full PR-equivalent gate
```
