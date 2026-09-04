---
doc_type: contributor
level: advanced
lifecycle: active
depends_on:
  - docs/learn/abi-api-handling.md
  - docs/_meta/topics.yaml
  - docs/_meta/terminology.yaml
  - mkdocs.yml
---

# Learning series — page specifications

**Companion to:** [Learning series — structure review and proposed
curriculum](learning-series-curriculum.md). That page decides *what* the
series should become (tiers, ownership rule, phases). This page goes one
level down and specifies *each artifact the phases produce*: the ladder
file and its generator, the per-page level table, the `topics.yaml`
changes, the rebuilt hub, and — for every new or reworked page — its
sections, the commands it runs, the cases it links, what it must *not*
restate, and how "done" is checked. Anyone can pick up one entry here and
ship it as a PR without re-deriving the plan.

**Conventions used below.** Every page spec has the same fields:

| Field | Meaning |
|---|---|
| Path / tier / level | File under `docs/learn/`, its ladder tier, its `level:` value |
| Ownership | The `topics.yaml` change the page ships with (§4.6 of the plan: case 1 link, case 2 take-over, case 3 cross-cutting) |
| Reader can… | The learning objective, phrased as what the reader can do afterwards |
| Prerequisites | Pages the reader is assumed to have read |
| Sections | H2/H3 outline with a one-line content note each |
| Runs | The invocations the page shows (real flags, copied from `docs/reference/cli-reference.md` or `github-action-inputs.md`) |
| Cases | Example-catalog cases linked (`docs/reference/examples/case*.md`) |
| Links, not restatements | Registered owners the page may only summarise and link |
| Footer | Previous / next in the ladder |
| Done when | Page-level acceptance, in addition to the gates every page passes |

Counts and line numbers in this page are a snapshot at the commit that
added it; the plan's §7 rule applies.

---

## A. Shared artifacts

### A1. `docs/_meta/learning-ladder.yaml`

> **2026-09-02 amendment.** As implemented, the file's `tiers:` are
> presented to readers as numbered **steps** (`Step 1` … `Step 9`, ids
> `1`-`9`; "tier" is the evidence-level word elsewhere in the docs), Part 7
> sits in Step 4 directly after Parts 2-6, and `mkdocs.yml`'s "ABI/API
> Compatibility" tab must carry one nav group per step in step order —
> `scripts/learning_ladder.py`'s sidebar rule, so the nav can no longer
> tell a different order from the ladder. The footer (A3) reads
> `· Step N · <title> ·` and links pages by their `short_title`. See the
> [curriculum plan's §5 amendment](learning-series-curriculum.md#5-proposed-target-shape)
> for why.

The one machine-readable owner of tier membership and reading order (plan
§5). It sits next to `topics.yaml` and `terminology.yaml`, and like them
it describes *ownership and order*, never content. The listing below is
the *target* shape after P8: P1 commits it with only the pages that exist
at that point, and each later PR appends the page it creates (§F), so
A2c's existence and completeness rules hold at every step.

```yaml
# docs/_meta/learning-ladder.yaml
#
# Two ordered reading sequences over docs/learn/. Every learn page except
# the hub belongs to exactly one sequence, as a member of exactly one tier.
# A tier may additionally *link* pages that are members elsewhere; a link
# never counts for completeness and is never checked for level.
# `branches` are optional side reads hanging off a member (Tier 2's "go
# deeper" pages): they must be >= the level of the page they hang from and
# are otherwise outside the sequence's monotonicity check.
# A page's level is declared once, in its own front matter. A tier's
# `floor:` is a property of the tier, not a second declaration of any
# page's level: every member must be >= it, so a whole tier cannot be
# quietly downgraded while the sequence stays monotonic.
# `paths:` are the hub's reading paths by role, as data, so the role table
# is rendered from the same source as the ladder and checked against it.

version: 1
hub: learn/abi-api-handling.md          # renders the ladder; exempt from it

sequences:
  educational:
    tab: "ABI/API Compatibility"
    tiers:
      - id: 0
        title: Orientation
        floor: beginner
        members:
          - learn/abi-series/abi-in-5-minutes.md
          - learn/how-a-break-shows-up.md
          - learn/abi-cheat-sheet.md
          - learn/abi-series/glossary.md
      - id: 1
        title: Foundations
        floor: beginner            # first member; last member is intermediate
        members:
          - learn/abi-series/00-product-contract.md
          - learn/abi-series/01-foundations.md
          - learn/abi-surface.md
      - id: 2
        title: Mechanics
        floor: intermediate
        members:
          - learn/abi-series/02-symbol-contracts.md
          - learn/abi-series/03-type-layout.md
          - page: learn/abi-series/04-cpp-abi.md
            branches:
              - learn/class-layout-abi.md
              - learn/exception-unwinding-abi.md
              - learn/modern-cpp-toolchain-hazards.md
          - page: learn/abi-series/05-linker-elf.md
            branches:
              - learn/msvc-pe-abi-model.md
          - learn/abi-series/06-transitive-breaks.md
      - id: 3
        title: Define the contract
        floor: intermediate
        members:
          - learn/compatibility-direction.md
          - learn/consumer-models.md
          - learn/build-profile-comparability.md
          - learn/static-and-header-only.md
        links:
          - learn/contract-aware-compatibility.md   # member of `concepts`
      - id: 4
        title: Evidence and detection
        floor: intermediate
        members:
          - learn/abi-series/08-detection.md
          - learn/assurance-methods.md
        links:
          - learn/evidence-and-detectability.md
          - learn/what-each-level-sees.md
      - id: 5
        title: Practice
        floor: intermediate
        members:
          - learn/where-in-the-pipeline.md
          - learn/surface-growth.md
          - learn/rollout-and-governance.md
          - learn/triage-a-finding.md
        links:
          - use/baseline-management.md              # tool track, case 1
      - id: 6
        title: Design
        floor: intermediate
        members:
          - learn/abi-series/07-designing-for-stability.md
      - id: 7
        title: At scale
        floor: advanced
        members:
          - learn/products-not-libraries.md
          - learn/template-heavy-libraries.md
          - learn/system-library-discipline.md
          - learn/dependency-floors.md
          - learn/environment-drift.md
          - learn/packages-and-consumers.md
      - id: 8
        title: Beyond static ABI
        floor: advanced
        members:
          - learn/behavioral-compatibility.md
          - learn/data-wire-compatibility.md
          - learn/ownership-and-lifetime.md
          - learn/concurrency-and-initialization.md

  concepts:
    tab: Concepts
    tiers:
      - id: c1
        title: Reading a result
        floor: intermediate
        members:
          - learn/verdicts.md
          - learn/contract-aware-compatibility.md
      - id: c2
        title: The evidence model
        floor: intermediate
        members:
          - learn/evidence-and-detectability.md
          - learn/what-each-level-sees.md
          - learn/elf-symbol-filtering.md
          - learn/limitations.md
      - id: c3
        title: Internals
        floor: advanced
        members:
          - learn/architecture.md
          - learn/build-source-data.md
          - learn/graph-coverage.md
          - learn/impact-analysis.md

paths:
  - role: New C/C++ library author
    pages:
      - learn/abi-series/abi-in-5-minutes.md
      - learn/how-a-break-shows-up.md
      - learn/abi-series/00-product-contract.md
      - learn/abi-series/01-foundations.md
      - learn/abi-series/02-symbol-contracts.md
      - learn/abi-series/03-type-layout.md
      - learn/abi-series/07-designing-for-stability.md
    after: start/choose-your-workflow.md    # tool-track hand-off; not ordered
  - role: C++ library maintainer
    pages:
      - learn/abi-series/01-foundations.md
      - learn/abi-series/04-cpp-abi.md
      - learn/class-layout-abi.md              # branch of Part 4
      - learn/abi-series/06-transitive-breaks.md
      - learn/abi-series/07-designing-for-stability.md
      - learn/template-heavy-libraries.md
    after: start/choose-your-workflow.md
  - role: CI / release engineer
    pages:
      - learn/how-a-break-shows-up.md
      - learn/compatibility-direction.md
      - learn/abi-series/08-detection.md
      - learn/where-in-the-pipeline.md
      - learn/surface-growth.md
      - learn/rollout-and-governance.md
      - learn/verdicts.md                      # concepts, after the educational tiers
    after: start/choose-your-workflow.md
  - role: Distribution / package maintainer
    pages:
      - learn/abi-series/05-linker-elf.md
      - learn/products-not-libraries.md
      - learn/system-library-discipline.md
      - learn/dependency-floors.md
      - learn/packages-and-consumers.md
    after: start/choose-your-workflow.md
  - role: Product / SDK owner (several binaries)
    pages:
      - learn/abi-series/00-product-contract.md
      - learn/consumer-models.md
      - learn/where-in-the-pipeline.md
      - learn/products-not-libraries.md
      - learn/template-heavy-libraries.md
    after: start/choose-your-workflow.md
  - role: Plugin / SDK author
    pages:
      - learn/abi-series/02-symbol-contracts.md
      - learn/compatibility-direction.md
      - learn/consumer-models.md
      - learn/rollout-and-governance.md
      - learn/abi-series/07-designing-for-stability.md
    after: start/choose-your-workflow.md
  - role: AI agent / automated reviewer
    pages:
      - learn/how-a-break-shows-up.md
      - learn/triage-a-finding.md
      - learn/verdicts.md
      - learn/evidence-and-detectability.md
    after: use/output-formats.md
```

Rules the file encodes (checked by `check_docs_contract.py`, A2c — the
generator only renders):

- **Completeness.** Every `docs/learn/**/*.md` except `hub` appears exactly
  once as a member or branch across both sequences. A file listed that does
  not exist, or a learn page listed nowhere, is an error. A `paths:` or
  `links:` mention never counts as placement.
- **Monotonicity per sequence.** Walking a sequence's members in order,
  each page's front-matter `level:` is ≥ the previous member's. Branches
  are checked only against the page they hang from. Links are never
  checked.
- **Level is declared once; floors are data.** A page's `level:` lives in
  its front matter and nowhere else; the ladder file never states a page's
  level. Each tier carries a `floor:` (a real key — a comment would be
  discarded by `yaml.safe_load`, leaving nothing to check), and the check
  reads every member's real level and fails if one is below its tier's
  floor. Monotonicity alone would let a whole advanced tier be downgraded
  to intermediate without failing; the floor closes that. A tier may span
  two levels, as tier 1 does.
- **Paths are walks up the ladder.** Every `paths:` entry names only
  members or branches. Each page resolves to its full ladder index
  (sequence, tier, member position, branch ordinal — a branch sorts right
  after the page it hangs from and before that page's next member, and
  sibling branches keep their YAML order, so a parent-to-own-branch step
  and a first-to-second-branch step are both increases), and walking
  `pages` that index is *strictly* increasing: no
  same-tier reversal, no repeated page, and `concepts` only after the
  entry's `educational` pages. Comparing tier positions alone would let
  two pages of one tier appear in reverse order, which is exactly the
  competing reading order the hub is meant to lose. `after:` is the one
  tool-track hand-off (a `start/` or `use/` page) and is not ordered.
- **Links must be members elsewhere.** A `links:` entry that is not a
  member of some tier (in either sequence, or a `use/` page) is an error —
  a link is a pointer, not a place to hide an unclassified page.

### A2. `scripts/gen_learning_ladder.py`

The generator that renders the hub's ladder from A1, following the
sentinel-splice pattern ADR-051 established for `gen_platform_matrix.py`.

| Aspect | Contract |
|---|---|
| Inputs | `docs/_meta/learning-ladder.yaml`; the front matter (`level`, `title` if present, else the first H1) of every page it names |
| Output | Replaces the block between `<!-- BEGIN GENERATED: learning-ladder -->` and `<!-- END GENERATED: learning-ladder -->` in `docs/learn/abi-api-handling.md` with one table per sequence: Tier · Level badge · Pages (members as links; branches indented with "go deeper"; links marked "(on the Concepts tab)" / "(tool guide)") |
| Output, continued | The hub's role table (A7 §4) from `paths:`, in a second sentinel block `learning-paths`, so the two tables cannot drift from the file |
| `--check` | Regenerates in memory and diffs against the file: drift only, exit 1 with the diff. It does not re-implement A1's rules — those live in `check_docs_contract.py` (A2c), so one gate owns every `docs/_meta/*.yaml` contract and the generator stays a renderer, like `gen_platform_matrix.py` |
| Wiring | `docs/AGENTS.md` "Regenerating generated docs" gains one line; `scripts/CLAUDE.md`'s inventory table gains the script; both sentinel blocks join `GENERATED_FILE_MARKERS` in `scripts/check_ai_readiness.py` (a hand edit inside a block is then caught, as for the platform matrix); `scripts/verify.py` gains a `learning-ladder` drift step in the `pr` profile. All of it lands in P1, together with the levels the rules read — a rule without its data fails its own gate, which is why P1 is one PR |
| Tests | `tests/test_gen_learning_ladder.py`: a round-trip test (`gen` then `--check` is clean), a drift test (an edited block exits 1), and a missing-sentinel test. The rule fixtures live with the contract check (A2c) |
| Not in scope | Reordering `mkdocs.yml` nav (that stays hand-edited; A2b checks it) |

**A2b. Nav-order check.** A `learning-nav-order` check in
`scripts/check_ai_readiness.py`, implemented in a sibling leaf module
(`scripts/learning_nav_order.py`, since the main script is past the
2000-line cap) that reuses the nav reader `mkdocs-nav-coverage` already
has (`_collect_mkdocs_nav_refs`) rather than parsing `mkdocs.yml` a second
time. For each group under the ABI/API Compatibility and Concepts tabs it
asserts the pages' `level:` values are non-decreasing in nav order,
skipping only the hub. Branches are included: A1 exempts them from the
ladder spine's monotonicity, not from the sidebar's. That is why P1 also
reorders three groups, inside the group each time so the recorded
by-question grouping is kept: in ABI Mechanics, `class-layout-abi.md`
(advanced) sits between Parts 4 and 5 (intermediate) today and moves
after Part 6; in Beyond ABI, `static-and-header-only.md` (intermediate)
sits fourth among the four pages A4 reconciles to advanced and moves to
the top of the group; in Concepts, `elf-symbol-filtering.md` and
`limitations.md` (intermediate) sit after the pages A4 reconciles to
advanced (`architecture.md`, `build-source-data.md`, `graph-coverage.md`,
`impact-analysis.md`) and move ahead of them, which is also A1's c2 → c3
order. `environment-drift.md` (advanced) stays last in Concepts until C13
moves it, so the group passes in between. This is the plan's Goal
criterion "each nav group is non-decreasing in level", made executable.

**A2c. Ladder rules in `check_docs_contract.py`.** A `_check_learning_ladder`
section next to the `topics.yaml` and `terminology.yaml` checks: loads
A1, then enforces completeness, monotonicity, floors, links-are-members,
paths-are-walks, and the footer rule from A3 (each page's previous/next
links match the ladder). Fixtures in the check's own test module: (a) a
page missing from the ladder, (b) a level regression inside a sequence,
(c) a branch below its parent, (d) a link that is nowhere a member, (e) a
path that steps down a tier, (f) a footer pointing at the wrong neighbour,
(g) a path listing two members of one tier in reverse order, (h) a path
repeating a page, and (i) the concepts sequence restarting at `intermediate` after
`educational` ends at `advanced` — which must *pass*.

**A2d. Anchor validation.** `mkdocs.yml` gains a `validation:` block with
`anchors: warn` (MkDocs 1.6+), so a link to a heading that no longer
exists is reported; under `--strict` a warning fails the build, which is
what A7's anchor rewrites need. Land it as `anchors: info` first if the
head has existing anchor warnings, and promote to `warn` in the same PR
once they are fixed — the plan's "one-off fragment check" then has a
permanent owner.

### A3. The page footer

One shape for every learning page that is not a numbered Part. The Parts
keep their breadcrumb, and their existing "Next" line must name the
ladder's next member — the A2c footer rule reads either shape. Four of
them do not today, and P1 rewrites those: Part 1 points at Part 2 where
the ladder's next member is `abi-surface.md`; Part 6 points at Part 7
where it is `compatibility-direction.md`; Part 7 points at Detecting
Breaks, which the ladder places earlier, so its next is the first Tier 7
member that exists — `dependency-floors.md` in P1, and B6 once P7 lands
(A2c reads the ladder as committed at each step, so both are correct in
their turn); and Detecting Breaks has no next at all, so it gains
`assurance-methods.md`. Placed after the last section, before any
"See also":

```markdown
---

**Ladder:** ← [Previous page title](prev.md) · Tier N · [Next page title](next.md) →
```

- "Previous"/"Next" follow A1's member order within the sequence; the last
  member of a tier points to the first member of the next tier; the last
  page of a sequence points back to the hub.
- Branch pages point "previous" at the page they hang from and "next" at
  that page's own next, so a reader who took the branch rejoins the spine.
- The footer is hand-written and *verified* by A2c (it knows the order,
  so it asserts each footer's two links match). Generating the footer
  itself is deliberately not done: a page's tail is prose, and a splice
  block in every page is more churn than a check. Material's
  `navigation.footer` feature was considered and rejected: it follows nav
  order, not ladder order, and cannot express a branch rejoining the spine.
- Each new page changes its two neighbours' footers. Section F batches the
  Tier 5 and Tier 7 pages two per PR so a neighbour's footer is edited
  once, not once per page.
- A footer names the *then-current* neighbour. The Footer fields below
  state the target ladder; when a page lands before its neighbour exists,
  it points at the nearest member that does (B3 → Part 7 until P6 creates
  B4; B7 → `dependency-floors.md` until P8 creates B8), and the PR that
  creates the missing page rewrites both footers. A2c checks the ladder
  as committed at each step, so both states pass in their turn.

### A4. Per-page level and placement

Every learn page, its level today, its target, and the action. A1 owns
placement; this table is the per-page worklist, and its Action column
names the B or C entry that carries the edit. "Reconcile" means the value
changes in Phase 1 (plan §6, Phase 1, fourth bullet).

| Page | Today | Target | Sequence / tier | Action |
|---|---|---|---|---|
| `abi-api-handling.md` | — | (hub, exempt) | — | rebuild (A7) |
| `abi-series/abi-in-5-minutes.md` | — | beginner | edu 0 | add level, footer |
| `how-a-break-shows-up.md` | (new) | beginner | edu 0 | B1 |
| `abi-cheat-sheet.md` | — | beginner | edu 0 | add level, footer; verdict table → legend + link (C7) |
| `abi-series/glossary.md` | — | beginner | edu 0 | add level, footer; absorb Part 1 §8 (C2) |
| `abi-series/00-product-contract.md` | — | beginner | edu 1 | add level; compatibility ladder section (C1) |
| `abi-series/01-foundations.md` | — | beginner | edu 1 | add level; drop §8 (C2) |
| `abi-surface.md` | intermediate | intermediate | edu 1 | footer; runnable boundary check (C14) |
| `abi-series/02-symbol-contracts.md` | — | intermediate | edu 2 | add level; one invocation + case links exist |
| `abi-series/03-type-layout.md` | — | intermediate | edu 2 | add level; hand C++ class layout to owner (C3) |
| `abi-series/04-cpp-abi.md` | — | intermediate | edu 2 | add level; shorten three summaries, §7 → summary (C3) |
| `class-layout-abi.md` | advanced | advanced | edu 2 branch of Part 4 | footer; nav row moves after Part 6 (A2b); becomes `class-layout` owner (C3) |
| `exception-unwinding-abi.md` | advanced | advanced | edu 2 branch of Part 4 | footer; one command, `case130` (C15) |
| `modern-cpp-toolchain-hazards.md` | advanced | advanced | edu 2 branch of Part 4 | footer; `case131` on the RTTI paragraph (C15) |
| `abi-series/05-linker-elf.md` | — | intermediate | edu 2 | add level |
| `msvc-pe-abi-model.md` | intermediate | advanced | edu 2 branch of Part 5 | reconcile; Windows worked example (C12) |
| `abi-series/06-transitive-breaks.md` | intermediate | intermediate | edu 2 | (already has front matter) |
| `compatibility-direction.md` | intermediate | intermediate | edu 3 | footer; show the reversed-arguments run once more explicitly next to the "once per direction" sentence (C15) |
| `consumer-models.md` | intermediate | intermediate | edu 3 | footer; one `--used-by` and one `--required-symbol` run (C15) |
| `build-profile-comparability.md` | intermediate | intermediate | edu 3 | footer; probe-matrix link (C16) |
| `static-and-header-only.md` | intermediate | intermediate | edu 3 | footer; nav row moves to the top of Beyond ABI (A2b); `case191` compare (C15) |
| `abi-series/08-detection.md` | — | intermediate | edu 4 | add level; §1a grows, §2 shrinks (C5) |
| `assurance-methods.md` | intermediate | intermediate | edu 4 | footer |
| `where-in-the-pipeline.md` | (new) | intermediate | edu 5 | B2 |
| `surface-growth.md` | (new) | intermediate | edu 5 | B3 |
| `rollout-and-governance.md` | (new) | intermediate | edu 5 | B4 |
| `triage-a-finding.md` | (new) | intermediate | edu 5 | B5 |
| `abi-series/07-designing-for-stability.md` | — | intermediate | edu 6 | add level; idiom section, CI section → link, single Next (C4) |
| `products-not-libraries.md` | (new) | advanced | edu 7 | B6 |
| `template-heavy-libraries.md` | (new) | advanced | edu 7 | B7 |
| `system-library-discipline.md` | (new) | advanced | edu 7 | B8 |
| `dependency-floors.md` | — | advanced | edu 7 | add level, footer |
| `environment-drift.md` | — | advanced | edu 7 | add level, footer; tab move; `case170` (C13) |
| `packages-and-consumers.md` | (new) | advanced | edu 7 | B9 |
| `behavioral-compatibility.md` | intermediate | advanced | edu 8 | reconcile; opening → sentence + link (C6); non-static check line (C15) |
| `data-wire-compatibility.md` | intermediate | advanced | edu 8 | reconcile; same |
| `ownership-and-lifetime.md` | intermediate | advanced | edu 8 | reconcile; same |
| `concurrency-and-initialization.md` | intermediate | advanced | edu 8 | reconcile; same; drop the "why this is its own page" section |
| `verdicts.md` | beginner | intermediate | concepts c1 | reconcile; footer |
| `contract-aware-compatibility.md` | intermediate | intermediate | concepts c1 | footer |
| `evidence-and-detectability.md` | intermediate | intermediate | concepts c2 | footer; appendix → archive (C8); §5 becomes the "cannot decide" owner (C6) |
| `what-each-level-sees.md` | intermediate | intermediate | concepts c2 | forward Next (C11) |
| `elf-symbol-filtering.md` | intermediate | intermediate | concepts c2 | footer; nav row moves ahead of the advanced block (A2b) |
| `limitations.md` | intermediate | intermediate | concepts c2 | footer; nav row moves ahead of the advanced block (A2b) |
| `architecture.md` | intermediate | advanced | concepts c3 | reconcile; verdict table → link (C7) |
| `build-source-data.md` | — | advanced | concepts c3 | add level; split (C9) |
| `graph-coverage.md` | — | advanced | concepts c3 | add level; pass-state detail → reference (C10) |
| `impact-analysis.md` | intermediate | advanced | concepts c3 | reconcile; plan/ADR framing out, field detail → reference (C10) |

### A5. `topics.yaml` changes

Fragments to add or edit, keyed the way the file is today. Fact sources
name the code that produces what each page teaches; `check_docs_contract.py`
checks that each path exists. The review-trigger script
(`check_docs_review_triggers.py`) reads a page's own front-matter
`depends_on`, not the registry, so every new page below also carries a
`depends_on:` listing the same paths as its `fact_sources`.

```yaml
  # --- new topics (Phase 3) ---

  break-symptoms:                       # B1
    canonical_page: learn/how-a-break-shows-up.md
    fact_sources:
      - scripts/evidence_tiers.py
    allowed_summaries:
      - learn/abi-api-handling.md
      - start/first-report.md

  compatibility-pipeline:               # B2, cross-cutting (case 3)
    canonical_page: learn/where-in-the-pipeline.md
    fact_sources:
      - action/
      - .github/workflows/publish-baseline.yml
      - .github/workflows/update-main-baseline.yml
    allowed_summaries:
      - learn/abi-api-handling.md
      - use/ci-gating.md

  surface-growth:                       # B3
    canonical_page: learn/surface-growth.md
    fact_sources:
      - abicheck/diff_surface_metrics.py
      - abicheck/surface_graph.py
      - abicheck/policy/severity.py
      - abicheck/semver.py
    task_pages:
      - use/api-surface-intelligence.md
      - use/annotations.md
    allowed_summaries:
      - learn/abi-api-handling.md
      - learn/abi-cheat-sheet.md

  compatibility-governance:             # B4, cross-cutting (case 3)
    canonical_page: learn/rollout-and-governance.md
    fact_sources:
      - abicheck/suppression.py
      - abicheck/policy_file.py
    allowed_summaries:
      - learn/abi-api-handling.md

  finding-triage:                       # B5, cross-cutting (case 3)
    canonical_page: learn/triage-a-finding.md
    fact_sources:
      - abicheck/comparability.py
      - abicheck/elf_symbol_filter.py
    allowed_summaries:
      - use/troubleshooting.md
      - start/first-report.md

  template-library-contract:            # B7
    canonical_page: learn/template-heavy-libraries.md
    fact_sources:
      - abicheck/bundle_manifest.py
      - abicheck/buildsource/source_abi.py
    allowed_summaries:
      - learn/abi-series/04-cpp-abi.md
      - learn/limitations.md

  system-library-discipline:            # B8
    canonical_page: learn/system-library-discipline.md
    fact_sources:
      - abicheck/policies/glibc_symbol_versioned.yaml
      - abicheck/diff_versioning.py
    allowed_summaries:
      - learn/abi-series/05-linker-elf.md
      - learn/abi-series/07-designing-for-stability.md

  packages-and-consumers:               # B9
    canonical_page: learn/packages-and-consumers.md
    fact_sources:
      - abicheck/package.py
      - abicheck/debian_symbols.py
    task_pages:
      - use/python-extensions.md
      - use/debian-symbols.md
      - start/scanning-conda-packages.md
      - integration/scenarios/packages-and-sdks.md
    allowed_summaries:
      - learn/abi-series/01-foundations.md

  class-layout:                         # C3 (Phase 2)
    canonical_page: learn/class-layout-abi.md
    fact_sources:
      - abicheck/diff_types.py
      - abicheck/diff_elf_layout.py
    allowed_summaries:
      - learn/abi-series/03-type-layout.md
      - learn/abi-series/04-cpp-abi.md

  # --- edits to existing topics ---

  bundle-analysis:                      # B6 takes ownership (case 2)
    canonical_page: learn/products-not-libraries.md
    task_pages:
      - use/multi-binary.md             # was canonical_page
      - integration/scenarios/release-bundle.md
      - integration/scenarios/multi-dso-project.md
    # fact_sources and reference_page (reference/cli-reference.md, the
    # exhaustive flag reference B6 keeps deferring to) unchanged

  evidence-model:
    allowed_summaries:
      # existing entries stay; add:
      - learn/abi-series/08-detection.md
      - learn/build-source-data.md
      - learn/how-a-break-shows-up.md
      - learn/where-in-the-pipeline.md    # depth per moment (B2 §2, §4)
      - learn/triage-a-finding.md         # evidence parity (B5 §1)

  verdicts:
    allowed_summaries:
      # existing entries stay; add:
      - learn/abi-cheat-sheet.md
      - learn/abi-series/07-designing-for-stability.md

  ast-frontend-resolution:
    allowed_summaries:
      # existing entries stay; add:
      - learn/abi-series/08-detection.md

  baseline-lifecycle:
    allowed_summaries:
      # existing entries stay; add:
      - learn/where-in-the-pipeline.md

  policies:
    allowed_summaries:
      # existing entries stay (the skills-src fragment declares `summarizes`); add:
      - learn/rollout-and-governance.md
  suppressions:
    allowed_summaries:
      # existing entries stay; add:
      - learn/rollout-and-governance.md
  github-actions-surface:
    allowed_summaries:
      # existing entries stay; add:
      - learn/where-in-the-pipeline.md
  project-integration:
    allowed_summaries:
      # no entries today; add:
      - learn/where-in-the-pipeline.md
      - learn/products-not-libraries.md
      - learn/rollout-and-governance.md   # B4 summarises the migration scenario (S26/S27, a task page here)
  impact-analysis:
    # reference_page stays reference/source-graph-schema.md; C10 moves
    # the field prose there, not to a new page
  build-target-scoping:
    # canonical stays learn/build-source-data.md and reference_page stays
    # reference/cli-reference.md; C9 moves sections to existing owners
```

Every `allowed_summaries` fragment above is an *addition* to the list the
topic already has — a page currently registered (several `skills-src/`
fragments declare a matching `summarizes`) must stay, or the front-matter
round trip fails. Two more things the fragments do not do, on purpose:
they never register a second `canonical_page` for a topic (the gate
rejects it), and they keep `baseline-lifecycle` on
`use/baseline-management.md` (plan §4.6 case 1).
Each fragment lands in the PR that creates the page it names (§F), never
earlier: a path that does not exist yet is a hard error, so the
`bundle-analysis` re-registration ships with B6, the `evidence-model`
addition for `learn/how-a-break-shows-up.md` with B1, and so on.
Exact `fact_sources` paths are to be confirmed against the tree when each
page is written; a wrong path is the same hard error in
`check_docs_contract.py`, so it cannot ship silently. `use/troubleshooting.md` and `limitations.md`
own no registered topic today, so B5 links them and registers nothing
about them — a `summarizes` entry naming a topic that does not exist is
itself a contract error.

### A6. `terminology.yaml` addition

```yaml
  Authority rule:
    canonical_page: learn/evidence-and-detectability.md
    short_definition: >-
      Artifact evidence (L0–L2) decides any BREAKING verdict; build and
      source evidence (L3–L5) can add, explain, scope or localise findings
      but never delete an artifact-proven break.
```

After registration, the restatements on the hub, `build-source-data.md`
§1, `what-each-level-sees.md` and the glossary become one sentence plus a
link (the terminology check warns on a bolded re-definition elsewhere).

### A7. The rebuilt hub (`learn/abi-api-handling.md`)

Target length: roughly a third of today's page. Section order:

1. **Title + two-sentence purpose.** "This series teaches ABI/API
   compatibility from first principles to running a scanner on a
   multi-binary product in CI. Start at Tier 0; each tier assumes the
   previous ones."
2. **Start here** (three links, in order): ABI in Five Minutes → How a
   break shows up → Part 0. This replaces the "Don't start here"
   admonition.
3. **The ladder** — the generated block (A2): the educational sequence's
   nine tiers, then the Concepts sequence, each row with a level badge.
4. **Reading paths by role** — the existing table, rewritten so each path
   is a subsequence of the ladder (E below), rendered from A1's `paths:`
   by the generator, never a jump to a `use/` page
   that the ladder does not reach through a tier link.
5. **The one idea** — the existing "compiler bakes the facts" paragraph,
   kept verbatim; it is the series' thesis.
6. **ABI and API, defined** — the two definitions stay here because the
   hub is `terminology.yaml`'s defining page for both terms.
7. **Where the tool track begins** — three sentences pointing at
   `start/getting-started.md`, `start/choose-your-workflow.md`, and the
   Concepts sequence.

What leaves the hub, and where it goes: the 23-row break-family index →
the cheat sheet (which already indexes cases by change, so the two tables
merge into one there); the "Going deeper than artifacts" and "The L5
graph" sections → one paragraph each on `08-detection.md` linking the
trio and `impact-analysis.md`; the "Now run it" table → B2 (Phase 3),
staying on the hub until then; the "Feed abicheck .so + debug info +
headers" section → already owned by `limitations.md`'s recommendation
section, so it becomes a link.

**Anchors to rewrite** when those sections move (`mkdocs build --strict`
does not check fragments until A2d lands): `#break-families-at-a-glance`
(linked from `08-detection.md` twice),
`#going-deeper-than-artifacts-the-source-scan` (from
`what-each-level-sees.md`) and
`#the-l5-graph-reachability-not-just-structure` (from
`static-and-header-only.md` three times, `use/policies.md` and
`use/suppressions.md`) — four pages for the two "deeper" anchors, two of
them under `docs/use/`. Grep `abi-api-handling.md#` across `docs/` before
moving, and re-point each to the new owner's anchor.

### A8. Redirects

No file is renamed or deleted in Phases 1–4, so `redirect_maps` needs no
new entries. The one page that changes *tab* (`environment-drift.md`)
keeps its URL. If Phase 2's `reference/` moves create new files, the
narrative pages stay at their URLs and only gain links, so again no
redirects.

---

## B. New pages

### B1. How a break shows up

| Field | |
|---|---|
| Path / tier / level | `learn/how-a-break-shows-up.md` · edu 0 · beginner |
| Ownership | new topic `break-symptoms`; `allowed_summaries` of `evidence-model` (it introduces the ladder the trio owns) |
| Reader can… | name the eight ways a compatibility break surfaces, say which mechanism family each comes from, and say which kind of evidence first reveals it |
| Prerequisites | ABI in Five Minutes |
| Footer | ← ABI in Five Minutes · Tier 0 · ABI Cheat Sheet → |

Sections:

1. **A break is a symptom before it is a mechanism** — one paragraph: the
   series is organised by mechanism (symbols, layout, C++, linker), but a
   reader meets a break as something that *happened*.
2. **The eight symptoms** — one H3 each, ~6 lines: what the user sees, one
   real error line, the mechanism family (link to the Part), the first
   evidence level that shows it (link to the trio), one case:
   - link error, `undefined reference to …` → Part 2, L0, `case01`
   - load error, `symbol lookup error` / `version GLIBC_2.x not found` →
     Part 5, L0, `case65`
   - crash or silent corruption after an upgrade with no rebuild → Parts 3
     and 4, L1, `case07`
   - compile error after an upgrade → Part 6, L2, `case123`
   - a call silently binds to a different value → Part 6, L2 (`case124`
     for the header-constant variant)
   - the source you compile against changed, but no binary did → Part 6,
     L4: a public macro or inline function disappeared (`case156`,
     `case157`), or an uninstantiated template's signature moved
     (`case122`). A *silent* behavioural change inside an inline body has
     no catalog fixture today; the page says so rather than pointing at
     a case that shows something else
   - works on the build box, fails on the customer's distro → dependency
     floors, `--env-matrix`, `case170`
   - works for the app, breaks the plugin or a sibling library → consumer
     models / bundle, `case90`
3. **The table** — the symptom → mechanism → level matrix from plan §4.1,
   one screen, no prose; each level cell is a link to that level's row on
   `what-each-level-sees.md`, never a typed definition of the level.
4. **What this means for you** — three bullets: "you cannot see the
   third row without debug info", "you cannot see the fourth without
   headers", "you cannot see the sixth from any binary" — each a link to
   the level's row on `what-each-level-sees.md`.

Runs: none on purpose (Tier 0 is pre-tool; the plan's acceptance
criterion exempts this one page by name). Cases: the ten above.
Links, not restatements: the trio for the levels; the Parts for the
mechanisms. Done when: every row of the table links one Part, one level
anchor and one case; the page is under 150 lines; no `ChangeKind` names
appear (they belong to the cheat sheet).

### B2. Where in the pipeline

| Field | |
|---|---|
| Path / tier / level | `learn/where-in-the-pipeline.md` · edu 5 · intermediate |
| Ownership | new cross-cutting topic `compatibility-pipeline`; `allowed_summaries` of `baseline-lifecycle`, `github-actions-surface`, `project-integration` |
| Reader can… | place the four moments a check can run (PR, merge to main, nightly, release cut), say what each one catches and costs, and explain why a break caught after merge re-fails every unrelated PR until re-baselined |
| Prerequisites | Tier 4; `use/baseline-management.md` (linked from Tier 5) |
| Footer | ← Assurance Beyond Static Checking (Tier 4's last member; the baseline page is a Tier 5 link, not a member) · Tier 5 · Report the surface → |

Sections:

1. **Four moments, two baselines** — a diagram (mermaid) with PR →
   merge → nightly → release, the accepted-main baseline refreshed at
   merge, the release-contract baseline refreshed at release cut. One
   paragraph per moment: what question it answers.
2. **The PR gate** — cheap tiers always; seeded source depth for the
   changed translation units; what the report must show (breaks *and*
   additions, link to B3). Run: `abicheck scan build/libfoo.so -H include/
   --sources . --against baseline.json --since origin/main --depth
   source` (without `--depth`, `scan` picks `auto`, which is risk-driven
   under a `--since` seed and may stop short of the source tier), and the
   Action equivalent in scan mode — `mode: scan`, `new-library`,
   `new-header`, `sources`, `depth: source`, `since: origin/main`, and
   `against` (or `abi-baseline`) for the baseline; `against` and `since`
   are scan-only inputs, so a compare-mode `old-library`/`new-library`
   pair cannot express the seeded scan.
3. **Merge to main** — refresh the accepted-main baseline; why skipping
   the *check* instead of relaxing the *gate* on a labelled PR poisons
   every later PR (`use/baseline-management.md` explains it in full; here,
   one paragraph and a link). Run: `abicheck dump build/libfoo.so -H
   include/ -o main-baseline.json`.
4. **Nightly** — the unseeded deep scan (`--depth source` without
   `--since`), the one-build audit (no `--against`), and where `--budget`
   and `--dry-run` fit. Run: `abicheck scan libfoo.so -H include/ --sources
   . --depth source --budget 15m`.
5. **Release cut** — publish the release-contract baseline; the release
   recommendation. Run: `abicheck compare last-release.json build/libfoo.so
   -H include/ --profile release-cut`.
6. **Cost against confidence** — a four-row table: moment · depth ·
   what it catches · typical cost bucket (link `docs/contribute/
   performance.md` for numbers; no numbers typed here).
7. **Several libraries or profiles** — one paragraph pointing at the
   `project` topology (S15, S25) and, once P7 lands, at B6; P5 links the
   scenarios only, and P7 adds the B6 link when it creates the page.

Runs: the four above, one per moment; every other depth/flag combination
is a link to `use/scan-levels.md` §Worked examples, which owns the
per-depth invocations. Cases: `case147` (a scan states the depth it
reached). Links, not restatements: baseline lifecycle and storage
(`use/baseline-*.md`), Action inputs (`reference/github-action-inputs.md`),
gating order (`use/ci-gating.md`), per-depth commands
(`use/scan-levels.md`). Done when: each moment has one runnable
command and one link to its how-to; the accepted-main/release-contract
distinction is explained in ≤ 2 paragraphs and links out.

### B3. Report the surface, not only the breaks

| Field | |
|---|---|
| Path / tier / level | `learn/surface-growth.md` · edu 5 · intermediate |
| Ownership | new topic `surface-growth` (criterion 2: "additions are compatible but not invisible" is a model no how-to states); `use/api-surface-intelligence.md` and `use/annotations.md` become its `task_pages` |
| Reader can… | explain why "0 breaks" is not "nothing to review", name the four signals that describe growth, and choose between reporting growth and gating on it |
| Prerequisites | Verdicts (Concepts c1), B2 |
| Footer | ← Where in the pipeline · Tier 5 · Rollout and governance → |

Sections:

1. **Growth is a change to the contract** — every added public symbol or
   type is a promise the next release has to keep; a frozen API treats
   growth as a break.
2. **Four signals** — one H3 each:
   - per-symbol additions in the report (the `COMPATIBLE (addition)`
     verdict category; `case03`, `case61`, `case62`);
   - aggregate roll-ups: `public_surface_grew`, `public_surface_shrank`,
     `undocumented_export_ratio_increased` — run: `abicheck compare
     old.json new.so -H include/ --surface-metrics --format json`;
   - the release recommendation (`release_recommendation` in the JSON
     report; `--profile release-cut`);
   - growth you did not intend: the one-build audit's accidental and
     unversioned exports (`case143`, `case145`).
3. **Report or gate?** — a `.abicheck.yml` `severity:` block with
   `addition: error` (or an Action `severity-preset` that includes it)
   turns additions into exit 1 — there is no dedicated Action input for
   additions; when that is right (a frozen API) and when it is noise (a
   growing SDK).
   Show the Action snippet from `use/github-action-recipes.md` §"Detect
   unintentional API expansion" by link, not copy.
4. **Make it visible on the PR** — `annotate: true` plus
   `annotate-additions: true` (the second has no effect without the
   first, per the input reference) for the notices,
   and the sticky comment recipe (link).
5. **Trend it** — a paragraph on keeping the roll-ups in a dashboard; no
   tooling claimed beyond the JSON fields.

Runs: the `--surface-metrics` compare; the `release-cut` compare. Cases:
03, 61, 62, 143, 145. Links, not restatements: severity categories
(`use/severity.md`), annotation mapping (`use/annotations.md`), pattern
verdicts (they are Part 7's, C4). Done when: all three growth features
are shown with the flag or input that enables them; the page never
implies a single "surface growth report" flag exists (plan §6 "Out of
scope" records that as a possible tool gap).

### B4. Rollout and governance

| Field | |
|---|---|
| Path / tier / level | `learn/rollout-and-governance.md` · edu 5 · intermediate |
| Ownership | new cross-cutting topic `compatibility-governance`; `allowed_summaries` of `policies`, `suppressions`, and `project-integration` (the migration scenario S26/S27 is one of its task pages) |
| Reader can… | take a project from no check to a gating check without a flag day, and write a suppression or policy override that says *who accepted what, until when* |
| Prerequisites | B2, Verdicts |
| Footer | ← Report the surface · Tier 5 · Triage a suspicious finding → |

Sections:

1. **Advisory first** — run the check, publish the report, fail nothing
   (`gate-mode: advisory` on the `check-target` Action, or the composite
   Action with `fail-on-breaking: false`; link S26 and
   `reference/check-target.md`).
2. **Then gate on the strongest signal only** — `fail-on-breaking` true,
   additions and risk advisory; widen later.
3. **An intentional break is a labelled, reviewed event** — the label
   relaxes the *gate*, never skips the *check* (link
   `use/baseline-management.md`); what the PR description must record.
4. **Suppressions are contract statements** — a rule has an owner, a
   reason and an expiry; `suppression.require_justification` and
   `suppression.strict` in `.abicheck.yml`; `--audit-suppressions` to list
   what is silencing what; the reachability-aware refusal (a rule that
   would hide a public-reachable break is refused unless explicitly
   overridden — link `use/suppressions.md` §Reachability-aware, cite
   `case192`).
5. **Policies name the contract shape** — `strict_abi`, `sdk_vendor`,
   `plugin_abi`, and the use-case profiles (`glibc_symbol_versioned` gets
   its full treatment in B8); `--pack` for reusable bundles of overrides
   and gate settings.
6. **A minimal `.abicheck.yml`** — one block with the `severity:` and
   `suppression:` keys the page has discussed, each line commented. The
   policy profile is selected with `--policy` (or the Action's `policy`
   input), not in the config file, which has no policy key — the page says
   so, since the reader will look for one.

Runs: `abicheck compare old.json new.so -H include/ --suppress
suppressions.yaml --audit-suppressions`; `abicheck compare … --policy
sdk_vendor`. Cases: 192, plus 118–120 (scoped internal changes as the
thing a policy is *for*). Links, not restatements: file format and
matching semantics (`use/suppressions.md`), profile contents
(`use/policies.md`), exit-code schemes (`use/ci-gating.md`). Done when:
the YAML block validates against `reference/config-keys-reference.md`;
every knob named is linked to its owner.

### B5. Triage a suspicious finding

| Field | |
|---|---|
| Path / tier / level | `learn/triage-a-finding.md` · edu 5 · intermediate |
| Ownership | new cross-cutting topic `finding-triage`; `allowed_summaries` of `evidence-model` (§1 summarises evidence parity). `use/troubleshooting.md` and `limitations.md` own no registered topic, so the page links them and registers nothing about them |
| Reader can… | given a finding that looks wrong, run a six-question check that ends in "real break", "wrong inputs", or "known limitation", and knows which flag re-runs the comparison to confirm |
| Prerequisites | Verdicts; `what-each-level-sees.md` |
| Footer | ← Rollout and governance · Tier 5 · Designing for Stability → |

Sections, each an H3 named as the question the reader asks:

1. **Did both sides get the same kind of evidence?** — a stripped side
   against a debug side, or headers on one side only, produces phantom
   findings; check the report's evidence block; `--debug-root` /
   `--debuginfod` to even them up.
2. **Were the headers the binary's headers?** — header/binary mismatch
   is the first suspect (`use/troubleshooting.md` §1); the compile
   context (Detecting Breaks, `08-detection.md` §1a) is the second.
3. **Were the two builds comparable at all?** — `NOT_COMPARABLE` / exit
   6 and the comparability gate (`build-profile-comparability.md`);
   `--diagnostic-comparison` to look anyway.
4. **Is this a symbols-only false positive?** — `elf-symbol-filtering.md`
   in one paragraph; the fix is headers, not a suppression.
5. **Is this a known limitation?** — templates, inline bodies, macros
   (`limitations.md`), with the L4 answer where one exists.
6. **Then it is real** — how to read the finding's own explanation
   fields (`compatibility_decision`, reachability state, the consumer
   call-chain under `--used-by`), and what to do next (Tier 6 patterns).

Runs: a compare with `--debug-root old=… --debug-root new=…` (the
option is repeatable and side-scoped, one token per side); a compare with
`--diagnostic-comparison`. Cases: 148 (header/build mismatch), 149 (ODR
variant), 150 (export/public pair). Links, not restatements: every
mechanism above is owned elsewhere; this page is a decision procedure
with links. Done when: the six questions form a strict order a reader
can follow top to bottom, and each ends with a named next action.

### B6. Products, not libraries

| Field | |
|---|---|
| Path / tier / level | `learn/products-not-libraries.md` · edu 7 · advanced |
| Ownership | takes `bundle-analysis` from `use/multi-binary.md` (plan §4.6 case 2); that page and the two scenarios become `task_pages` |
| Reader can… | state the bundle contract, tell a release bundle from a set of independent targets, run a directory compare and read a cross-library finding, and compare against stored bundle facts without the old binaries |
| Prerequisites | Tier 5; Part 5 (SONAME, `DT_NEEDED`) |
| Footer | ← Designing for Stability · Tier 7 · Template- and header-heavy libraries → |

Sections:

1. **A product is one contract** — a symbol one sibling imports from
   another is public *inside* the product even if hidden from users; a
   SONAME cohort; provider ownership of a symbol (which library defines
   it).
2. **Three shapes** — release bundle (S14, one report, cross-library
   findings), independent targets (S15, N reports, no cross-library
   claims), monorepo components (S25); a decision table with the
   `.abicheck.yml` `bundles:` / `targets:` shape each implies (link the
   integration scenarios; do not restate their YAML).
3. **Run it** — `abicheck compare release-1.0/ release-2.0/ -H include/
   --fail-on-removed-library`; what the per-library results and the
   bundle block of the JSON look like (link `use/multi-binary.md`
   §JSON output schema additions).
4. **The five cross-library findings** — one paragraph each, one case
   each: SONAME skew (`case84`), intra-bundle dependency removed
   (`case90`), intra-bundle signature drift (`case91`), provider changed
   (`case92`), manifest drift (`case93`).
5. **Declaring what the bundle promises** — `--instantiation-manifest`
   (renamed from `--manifest`, CLI cleanup phase two) with its three
   entry shapes (`pattern:`, `template:` + `instantiations:`, `symbol:`),
   with the template shape deferred to B7; `.abicheck.yml`'s `bundle:`
   block (`system_providers:` for libc/libstdc++-class providers,
   `cohorts:`) — CLI cleanup phase two demoted both off the CLI, replacing
   `--bundle-system-providers`/`--bundle-cohort`.
6. **Comparing against stored facts** — capture with
   `--bundle-facts-out` on one release, compare a later release by pointing
   `compare` at the stored `.bundlefacts.json` directly (OLD_INPUT is
   auto-classified as a stored BundleFacts document — no separate
   `--old-bundle-facts` flag) without re-opening the old binaries;
   per-library header roots and compile contexts for products whose
   libraries share one include tree.
7. **Fan-out and fan-in** — one check per target, `aggregate` folding
   the reports into one gate. `aggregate` needs to know the expected
   target set: `--run-plan plan.json` from `project plan` (the
   declarative form, recommended), `--manifest`, or an explicit
   `--discovered-only` to gate on whatever reports are present; without
   one of the three it exits 64 before reading a report, and the page
   says why (a missing report must be a failure, not an absence).
8. **What the bundle layer cannot do today** — ELF-only cross-library
   findings (per-library results everywhere); bundle checks at binary
   depth only in the declarative topology (README's migration blockers,
   linked, not copied).

Runs: the directory compare; a `--instantiation-manifest` compare; the
`--bundle-facts-out` capture / stored-BundleFacts compare pair; `abicheck aggregate
reports/ --run-plan plan.json` (and the `--discovered-only` form once,
labelled as the opt-out). Cases: 84, 90–93 — the bundle cases only.
`case151` (a single-build audit corroborating the header AST against the
source index) and `case162` (an exported declaration moving between
header files, L5) are evidence-provider cases, not product ones; both go
to Detecting Breaks (C5). Links, not restatements: flag reference (`use/multi-binary.md`),
aggregate axes (`use/aggregate-reports.md`), topology schema
(`reference/project-targets-schema.md`). Done when: `topics.yaml` shows
this page as `bundle-analysis`'s canonical page and `use/multi-binary.md`
as a task page in the same PR; all five bundle cases are linked; the
ELF-only limitation is stated.

### B7. Template- and header-heavy libraries

| Field | |
|---|---|
| Path / tier / level | `learn/template-heavy-libraries.md` · edu 7 · advanced |
| Ownership | new topic `template-library-contract` (criterion 2: "the explicit-instantiation matrix *is* the contract") |
| Reader can… | say what a template library actually exports, write the instantiation manifest for it, choose a scan depth and scope that finishes on a large template-heavy tree, and explain why "not comparable" beats a page of phantom additions |
| Prerequisites | Part 4 §3 (templates and inline), B6 |
| Footer | ← Products, not libraries · Tier 7 · How system libraries stay compatible → |

Sections:

1. **What a template library exports** — nothing until instantiated;
   implicit instantiations live in the *consumer's* binary; explicit
   instantiations are the only symbols the library owns (Part 4 §3 in one
   paragraph, link).
2. **The contract is the instantiation matrix** — the `--instantiation-manifest`
   `template:` + `instantiations:` shape; dozens of entries describing
   thousands of mangled symbols; bootstrapping the manifest from a dump
   (link `use/multi-binary.md` §Bootstrapping). Cases: 17, 79.
3. **What the header side can and cannot see** — castxml emits
   instantiations only; the clang L2 backend also records the
   uninstantiated pattern (`reference/header-backend-capabilities.md`);
   neither detects a change to an uninstantiated template's *signature*
   — that is L4's job (`case122`, the same uninstantiated-signature case
   B1 cites); default
   template arguments (`case87`), internal template signatures
   (`case85`), templated `detail::` bases (`case77`).
4. **The cost cliff** — the one cliff at L4 tracks template depth; what a
   full-target replay costs on a large tree versus a seeded one (link
   `docs/contribute/performance.md`; no numbers here); RAM-aware worker
   caps and the L4 cache, restored via the Actions cache; `--dry-run`
   before spending; `--budget` fails loudly rather than shrinking scope.
   Run: `abicheck scan libfoo.so -H include/ --sources . --depth source
   --since origin/main --dry-run`.
5. **Multi-TU surfaces and comparability** — `--dump-manifest` for a
   surface that is several translation units; the comparability gate
   refusing a manifest/flag mismatch with `NOT_COMPARABLE` rather than
   producing phantom additions and removals; `--diagnostic-comparison`
   as the escape hatch.
6. **Header-only libraries** — one paragraph and a link to
   `static-and-header-only.md` (Tier 3) for the shape without a binary;
   `case191` for a header-graph field-type change.

Runs: the seeded `--dry-run` scan; a `--instantiation-manifest` compare; a
`--dump-manifest` dump. Cases: 17, 77, 79, 85, 87, 122, 191. Links, not
restatements: backend capabilities (reference), performance numbers
(contribute), manifest schema (`use/multi-binary.md`). Done when: the
L2/L4 distinction on templates is stated exactly as the evidence tiers
record it; the manifest example is copied from the how-to's own tested
snippet, not typed fresh.

### B8. How system libraries stay compatible

| Field | |
|---|---|
| Path / tier / level | `learn/system-library-discipline.md` · edu 7 · advanced |
| Ownership | new topic `system-library-discipline` (criterion 4: a contract *strategy*, not another mechanism); Parts 5 and 7 registered as summaries |
| Reader can… | explain how glibc and libstdc++ ship one SONAME for decades, place their own library on the ladder of strategies, and pick the abicheck policy and cases that match |
| Prerequisites | Part 5 §3 (version scripts), Part 7 Pattern 4 |
| Footer | ← Template- and header-heavy libraries · Tier 7 · Dependency & Runtime Floors → |

Sections:

1. **The ladder of strategies** — the six-row table from plan §4.2
   (kernel, glibc, libstdc++, binutils/ld, vendor SDK, plugin), as the
   page's spine.
2. **glibc: one SONAME, append-only version nodes** — every ABI change
   is a new `GLIBC_2.x` node; the old symbol stays under its old node as
   a compat symbol; nothing is ever removed; what a consumer's binary
   records (`GLIBC_2.28` in `.gnu.version_r`) and why that *is* the floor
   (link Tier 7's floors page). Cases: 13, 65, 139, 141, 183.
3. **libstdc++: the dual ABI as a parallel namespace** — `GLIBCXX_3.4.x`
   nodes since 2004; `_GLIBCXX_USE_CXX11_ABI` as two coexisting layouts
   rather than a break; what a flip looks like in a report (`case104`,
   the dual-ABI flip detector; link `modern-cpp-toolchain-hazards.md`).
4. **binutils and the linker: defaults move the contract** — `DT_RELR`,
   RPATH vs RUNPATH, hash style, CET/static-TLS (link
   `environment-drift.md` §binutils), RELRO (link
   `use/security-hardening.md`); a library rebuilt on a newer toolchain
   changes contract without a source change.
5. **Adopting the discipline yourself** — version script per release
   node; `.symver`/compat aliases; the rule "remove only on a SONAME
   bump"; inline-namespace generations for C++ (`case99`–`case101`);
   experimental namespaces (`case100`).
6. **Checking it** — `abicheck compare old.so new.so --policy
   glibc_symbol_versioned` (version-node removals pinned to break,
   compat-version requirement additions accepted, dropped `DT_NEEDED` as
   risk); the one-build audit's unversioned-export finding (`case145`).

Runs: the `glibc_symbol_versioned` compare; `abicheck scan libfoo.so -H
include/` for the audit. Cases: 13, 65, 99, 100, 101, 104, 139, 141, 145,
183. Links, not restatements: version-script mechanics (Part 5 §3, Part 7
Pattern 4), floors (`dependency-floors.md`), drift
(`environment-drift.md`). Done when: every ladder row names a policy or
case; the page contains no claim about glibc or libstdc++ that is not
sourced to a Part or a case.

### B9. Packages and consumers

| Field | |
|---|---|
| Path / tier / level | `learn/packages-and-consumers.md` · edu 7 · advanced |
| Ownership | new topic `packages-and-consumers` (criterion 4: a new audience — packagers and binding authors); the Python, Debian and conda how-tos become `task_pages` |
| Reader can… | check a library from the artifact a distribution actually ships, and reason about consumers that are not C/C++ callers (bindings, extensions, wheels) |
| Prerequisites | Tier 5; `consumer-models.md` |
| Footer | ← Environment & Toolchain Drift · Tier 7 · Behavioral & Semantic Compatibility → |

Sections:

1. **The artifact is the package** — comparing `.rpm`/`.deb`/tar/conda
   inputs directly; debuginfo and devel packages as the evidence sources;
   two lines summarising the packages-and-SDKs scenario (S13, a registered
   task page) and a link to it. Run: `abicheck compare old.rpm new.rpm --debug-info old=old-dbg.rpm
   --debug-info new=new-dbg.rpm --devel-pkg old=old-devel.rpm --devel-pkg
   new=new-devel.rpm`, then one line each for the other two operand
   types `package.py` accepts the same way — `abicheck compare
   old.tar.gz new.tar.gz -H include/` and `abicheck compare old.conda
   new.conda -H include/` — since only the operand changes.
2. **Debian `symbols` files are a consumer-declared contract** — generate,
   validate, diff (link `use/debian-symbols.md`); the automatic check on
   a `.deb` compare. Run: `abicheck compare old.deb new.deb --debug-info
   old=old-dbgsym.deb --debug-info new=new-dbgsym.deb`.
3. **conda: the pieces live in different packages** — the umbrella-header
   trick and mapping a conda version to an upstream tag (link the
   scanning-conda page; two paragraphs).
4. **Python extensions** — why exports are the wrong surface; the
   limited API / `abi3` floor (`abicheck scan module.so --abi3 3.9`);
   the Python-level API as a surface (`case163`); manylinux and the
   glibc floor for wheels (link floors).
5. **FFI consumers** — Rust/Go/Python `ctypes` bind to the C ABI by
   declaration copies; what that means for the direction of the promise
   (link `compatibility-direction.md`) and for `--used-by` (a binding's
   loader is the app).
6. **Kernel and accelerator ABIs** — one paragraph each with a link
   (kABI/BTF, `case121`/`case175`/`case176`; SYCL host vs device,
   `case82`/`case126`) — the "other ABI domains" further-reading the
   plan defers, at pointer depth only.

Runs: the RPM and Debian compares in full, the tar and conda operand
variants one line each, the `--abi3` scan, and a `--required-symbol`
compare for the names a `dlopen`/`dlsym` binding resolves at runtime
(`--used-by` fits only a binding that has link-time imports from the
library; the interpreter itself imports none of the extension's entry
points, so scoping to it captures nothing). Cases: 121, 163, 170, 175, 176,
82, 126. Links, not restatements: all four task pages (the three how-tos
and S13). Done when: each of the four package formats has one runnable
command (RPM and Debian with their debug sidecars, tar and conda as the
operand variants above); the Python section never re-explains the
limited API beyond one paragraph.

---

## C. Reworked existing pages

Each entry names the exact section that changes and what replaces it.

### C1. Part 0 — the compatibility-levels ladder

Insert a new **§2a "Which level of promise are you making?"** between
today's §2 (dimensions) and §3 (public surface): the six-rung ladder from
plan §4.3 as a table with columns *promise · what the consumer may do ·
verdict that gates it · exit code · SemVer action · `--contract` domain
(where one applies)*. The existing §4 SemVer table then links up to this
one instead of re-deriving the mapping. No mechanism text; each rung
links its Part or Tier 3 page.

### C2. Part 1 §8 → glossary

Delete §8; replace with one sentence linking `abi-series/glossary.md`
("Terms used here are defined in the Glossary"). Any Part 1 term the
glossary lacks moves there
first (diff the two lists before deleting).

### C3. Class layout: one owner

- `class-layout-abi.md` gains `canonical_for: [class-layout]` and the
  A5 registration.
- Part 3 keeps §§ size/offset, alignment, enums, unions, bitfields; its
  C++ class-layout material becomes one paragraph ending in a link to
  the owner.
- Part 4 §7 "Base-class position and layout" (and its EBO/tail-padding
  H3) becomes a five-line summary plus link; the three hazard summaries
  (§"Exception unwinding", §"Modern C/C++ and toolchain ABI hazards", and
  the class-layout cross-reference) each shrink to one sentence plus link.

### C4. Part 7 — the scanner recognises these patterns

Add **"Pattern 7 — let the checker see the pattern"** after Pattern 6:
the idioms the scanner recognises from declaration facts (opaque pointer,
PIMPL, handle, factory, create/destroy pair, callback ABI), the anti-
patterns it flags (STL by value across the boundary, polymorphic type
without a virtual destructor), and what `--pattern-verdicts` does
(demote-with-reason on a provably opaque type; never delete; raise
`opaque_invariant_broken` / `handle_type_changed` when a guarantee is
lost). Run: `abicheck compare old.so new.so -H include/ --pattern-verdicts
--explain-patterns`. Cases: 80 (PIMPL shared→unique), 76 (PIMPL vtable).
The existing "Wiring abicheck into CI" section becomes three lines and a
link to B2; the page ends with one "Next" (B6, per the ladder).

### C5. Detecting Breaks (`08-detection.md`)

- §1a grows into the AST summary (registered under
  `ast-frontend-resolution`): add three short paragraphs — what a header
  AST dump *is* (`dump -H` produces one; the compile context decides its
  contents), the castxml/clang capability difference at pattern level
  (link the reference matrix), and "the same idea applied per translation
  unit" for L4, with the explicit note that an uninstantiated template's
  signature change is L4-only (`case122`); cite `case151` (two providers
  corroborating one declaration) and `case162` (a declaration's source
  file changing, which only L5 sees) in the same paragraphs. Keep the
  existing context table.
- §2 "What it takes to find each break family" shrinks to one paragraph
  and a link to `what-each-level-sees.md` §Reference (the table there is
  the owner).
- The opening "not Part 8" admonition becomes one sentence.

### C6. The four "cannot decide this" pages

- `evidence-and-detectability.md` §5 "What ABI tools cannot prove" is
  the owner; extend it by one paragraph naming the four dimensions.
- Each of the four pages replaces its opening argument with one sentence
  ("A static comparison cannot decide this dimension; §5 of Evidence &
  Detectability says why.") and starts on its own content.
- `concurrency-and-initialization.md` drops its "Why this belongs on its
  own page" section (the registry comment already records the decision).

### C7. Verdict tables

`verdicts.md` keeps the numeric table. `architecture.md`'s copy becomes
a link. `abi-cheat-sheet.md` and Part 7 keep a one-line legend (the six
icons with their names) and link; both are added to `verdicts`'
`allowed_summaries` (A5).

### C8. Evidence appendix

The "removed scan axes" appendix moves to `use/companion-commands.md`,
the CLI migration page, as a new `## Removed scan axes` section beside its
`## Removed commands`; the model page keeps one sentence pointing there.
That page has no front matter today, so it gains one with
`lifecycle: migration` — the retired-surface check exempts that lifecycle,
so the dead spellings are safe there without an allowlist entry.

### C9. `build-source-data.md` split

The page has no front matter today; it gains one (`level: advanced`, and
`canonical_for: [build-target-scoping]`, which the registry already names
it for). Stays: §Authority rule (as a link once A6 lands), §Evidence
layers (as a summary, C5), §How the data flows, §Workflow (all H3s,
including the Bazel scoping section the topic is registered for), §Inputs,
expectations & cost. Moves, each to the reference page that already owns
the subject — no new reference page, and `build-target-scoping`'s
`reference_page` stays `reference/cli-reference.md`: §What the data
actually looks like → `reference/build-output-schema.md` (the L3 record)
and `reference/source-graph-schema.md` (the L5 record); the four findings
lists (§Build-evidence, §Source ABI replay, §Source graph, §Cross-source
validation) → one paragraph each on the narrative page linking the kinds'
entries in the generated `reference/detector-spec.md`, since the list of
kinds is generated and must not be copied; §Evidence coverage / metrics →
`use/output-formats.md`; §Schema & storage → `reference/snapshot-format.md`.
The narrative page links each moved section at the point it was.

### C10. Graph coverage and impact assessment

`impact-analysis.md`: delete the "slice 1 of G29 Phase 3" opener and the
`contribute/plans/` links; keep the mental model (what reachability
means, the tri-state, the worked dispatcher scenario); move the
field-by-field description of `impact_assessment` and
`reachability_state` to `reference/source-graph-schema.md`, the topic's
registered `reference_page` already. `graph-coverage.md` (canonical of no
topic): keep the negative-evidence argument; move the pass-state field
names (`extractor_passes` and siblings) to the same reference page.

### C11. `what-each-level-sees.md` forward Next

Replace the closing "Next: Evidence & Detectability" with the A3 footer
(previous: Evidence & Detectability; next: `elf-symbol-filtering.md`),
and add one line under it: "Ready to run it? `use/scan-levels.md` owns
the `--depth` choice."

### C12. MSVC/PE worked example

Add **"A worked example on Windows"** to `msvc-pe-abi-model.md`, built
on the MSVC CI lane's own fixture (`tests/test_msvc_pdb_e2e.py` compiles
`foo.dll` twice with `cl.exe /Zi`, the second build growing the by-value
`Widget` struct): `abicheck compare v1\foo.dll v2\foo.dll` with each PDB
next to its DLL reports the struct growth as BREAKING, because the PDB
supplies the layout; the same compare with the PDBs removed shows only
the export table, in which the grown struct is invisible. Use that
PDB-dependent signal, not an export removal (`case01`-shaped breaks are
visible from the export table alone, so removing the PDB changes
nothing). The report does not say "PDB" — PDB-derived layout lands in the
same debug-info channel as DWARF and the report states evidence tiers
only — so the page explains the difference in prose and points at the
evidence tier that changes, not at a field. Reuse the test's own header
source rather than inventing a fixture.

### C13. `environment-drift.md`

Add `case170` to §The glibc side (it is the fixture); add front matter
(`level: advanced`); move to the Platforms & Toolchains nav group;
update `docs/AGENTS.md` "Layout" (the tool-track list loses this entry,
the educational list gains it).

### C14. `abi-surface.md` — make the boundary check runnable

§"Checking the boundary with abicheck" gains two runs: the one-build
audit `abicheck scan libfoo.so -H include/` (accidental export,
private-header leak — `case143`, `case144`) and a compare with
`--contract exports --no-scope-public-headers` showing an internal change
classified out of contract (`case118`). The second flag is required: the
default public-header scoping filters the unreachable change out before
contract evaluation classifies it, so with `--contract exports` alone the
report shows nothing.

### C15. Commands on concept pages without one

- `compatibility-direction.md`: keep the existing reversed-arguments
  command and repeat it inline where the page says "once per direction".
- `consumer-models.md`: one `--used-by ./app` run and one
  `--required-symbol plugin_init` run, each under the consumer shape it
  illustrates.
- `exception-unwinding-abi.md`: `abicheck compare old.so new.so
  --build-info old=build-old/ --build-info new=build-new/` showing the L3
  exceptions-mode flip (`case130`). `case131` is the RTTI-mode flip, not
  an exceptions one; it goes to `modern-cpp-toolchain-hazards.md`'s
  `-fno-rtti` paragraph, which today cites no case.
- `02-symbol-contracts.md`: one L0 compare at the top of §1 (`case01`).
- `assurance-methods.md`: no scanner command (by design); instead, one
  concrete non-static check per row (a consumer-rebuild job, a
  binary-swap smoke test), phrased as a shell line.
- the four Tier 8 pages, the same way: one non-static check each as a
  shell line — a consumer rebuild-and-test run (`behavioral-compatibility.md`),
  a stored file written by the old version and read back by the new one
  (`data-wire-compatibility.md`), a consumer run under AddressSanitizer
  (`ownership-and-lifetime.md`), a consumer run under ThreadSanitizer
  (`concurrency-and-initialization.md`). No case link: the catalog has no
  fixture for what static comparison cannot see, and the plan's Phase 4
  requirement is narrowed to say so.
- `static-and-header-only.md`: the header-only compare from `case191`
  (`--header old=… --header new=…`, `--ast-frontend clang`), copied from
  the case's own README.

### C16. `build-profile-comparability.md` — probe matrix

Add one paragraph and a run under the comparability section: `abicheck
compare old.so new.so --probe-matrix old=probes-old.yaml
--probe-matrix new=probes-new.yaml` as "compare one library across build
configurations", linking `use/probe-harness.md`.

---

## D. Content to remove

The plan's phases move and consolidate; this section lists what should be
**deleted** from today's pages because it is stale, a roadmap claim, a
third copy of a registered owner, or tool-track material inside a page
that is meant to teach. "Shrink" means one sentence and a link remain;
"delete" means nothing remains. Every deletion keeps its URL (no page is
removed) and, where a fragment is linked from elsewhere, the anchors in A7
are rewritten first.

| Page | Content | Why it does not belong | Action |
|---|---|---|---|
| hub | "New to the topic? Don't start here" admonition | contradicts the page's role as the published entry point (F2) | delete (A7 §2 replaces it) |
| hub | "Detection coverage and roadmap" | a roadmap claim ("areas still deepening…") in a learning page; goes stale with nothing to catch it; the count it gestures at has a fact owner | delete; the change-kind count stays with `reference/change-kinds.md` |
| hub | "Scope & assumptions" note | two long bullets restating Part 5's platform parallels and `limitations.md`'s detectability matrix | shrink to one line: "Examples are ELF/Linux unless a page says otherwise" + two links |
| hub | "Runtime calls are not the same as ABI dependencies" | full paragraph owned by `abi-surface.md` (registered `public-surface`) | shrink |
| hub | "App-swap (ASW): the consumer-scoped runtime check" | owned by `evidence-and-detectability.md` §4 and `use/appcompat.md` | shrink |
| hub | "Feed abicheck `.so` + debug info + headers" | owned by `limitations.md`'s recommendation section, verbatim command included | shrink |
| hub | "Which input proves which family" and "The L5 graph" | restate the trio and `impact-analysis.md`; the L5 section is the third statement of the authority rule (A6) | shrink |
| hub | the 23-row break-family index | the cheat sheet already indexes cases by change; two indexes drift | move into the cheat sheet's table (one index), delete here |
| hub | the phrase "nine-part learning series" | the page itself says the capstone is not Part 8; the count contradicts the spine | delete the count |
| `evidence-and-detectability.md` | Appendix "removed scan axes" | a changelog for flags that no longer exist; dead spellings in a live page | move to `use/companion-commands.md` (C8) |
| `evidence-and-detectability.md` | the self-correction about a previously over-claimed coverage figure | changelog material; a reader gains nothing from the history of a wrong number | delete |
| `evidence-and-detectability.md` | §2 b–d (libabigail, ABICC, abicheck compared) | `reference/tool-comparison.md` and `use/tool-modes.md` own the comparison | shrink to one paragraph + links; keep §2 a (app swap) and e (non-static methods) |
| `08-detection.md` | §3 "Why an abidiff- or ABICC-class checker is not sufficient" | the same tool comparison, a third time | shrink to the two-sentence claim + link to `reference/tool-comparison.md` |
| `08-detection.md` | the "not Part 8" admonition | navigation apology; the ladder makes the page's place explicit | delete (one sentence stays, C5) |
| `01-foundations.md` | §8 Glossary | duplicate of `glossary.md` | delete (C2) |
| `01-foundations.md` | §7 "Where abicheck fits" pipeline walkthrough | tool architecture inside the first educational Part; owned by `architecture.md` | shrink to one paragraph + link |
| `04-cpp-abi.md` | "How to design C++ libraries for ABI stability" | Part 7 owns design patterns; this is a second, shorter list | shrink to a link to Part 7 (and B7 for templates) |
| `04-cpp-abi.md` | inline summaries of the three split-out hazards | registered summaries, but at paragraph length they are the second treatment F3 describes | shrink to one sentence each (C3) |
| `05-linker-elf.md` | "How to govern the linker-level contract" tip box | five design rules duplicating Part 7 Pattern 4; the worked system-library example it lacks is B8 | shrink to a link to Part 7 and B8 |
| `05-linker-elf.md`, `06-transitive-breaks.md` | "CI gate on every PR" one-liners | practice advice repeated per Part; B2 owns it | delete; the Parts' "next" leads to Tier 5 anyway |
| `07-designing-for-stability.md` | "Wiring abicheck into CI" | B2 owns the pipeline; the verdict table there is C7's duplicate | shrink to three lines + link |
| `07-designing-for-stability.md` | second "next" target | one next per page (A3) | delete |
| `abi-cheat-sheet.md` | "Verdict Quick Reference" table | `verdicts.md` owns verdict meaning | shrink to a one-line legend (C7) |
| `abi-cheat-sheet.md` | closing link to the raw GitHub `examples/` tree | the encyclopedia is the published catalog; a raw-tree link bypasses the generated case pages | replace with `reference/examples/index.md` |
| `limitations.md` | "Dependency Limitations & Known Bugs" (the castxml `__has_cpp_attribute` / Xcode note) | a dated bug note in a boundaries page; `use/troubleshooting.md` §0 is where setup failures live and it already covers castxml aborts | move the paragraph to troubleshooting §0, delete here |
| `limitations.md` | "Troubleshooting" stub section | a heading with a link under it | delete; link from the page intro instead |
| `architecture.md` | the module map and per-module prose | contributor material; `docs/contribute/codebase-overview.md` owns the module map for site readers (the root `AGENTS.md` for agents) | shrink to the pipeline diagram + link to the codebase overview |
| `architecture.md` | verdict / exit-code table | `verdicts.md` owns it | shrink to a link (C7) |
| `build-source-data.md` | "Recommended defaults", "Time & resource model" | `use/scan-levels.md` §Cost guide and `contribute/performance.md` own them | shrink to links |
| `build-source-data.md` | schema, storage and redaction sections | reference material, not a mental model | move to the existing reference owners (C9) |
| `impact-analysis.md` | "slice 1 of G29 Phase 3 (ADR-052)" opener and `contribute/plans/` links | delivery history, not learning | delete (C10) |
| `graph-coverage.md` | "G31 Phase B" references and pass-state field names in prose | same | delete the phase references; fields move to the reference page (C10) |
| `concurrency-and-initialization.md` | "Why this belongs on its own page rather than folded into 'behavioral'" | editorial justification; the registry comment already records the decision | delete (C6) |
| the four "Beyond ABI" pages | the opening "a static comparer cannot decide this" argument | fourth restatement of `evidence-and-detectability.md` §5 | shrink to one sentence + link (C6) |
| `what-each-level-sees.md` | closing "Next: Evidence & Detectability" | a backwards next | replace with the A3 footer (C11) |
| `docs/index.md` | "Start with the learning series (part 0 assumes nothing…)" | sends newcomers past the on-ramp | reword to the Tier 0 entry (A7 §2) |

What is deliberately **not** removed, because a reviewer might expect it
to be: Part 4's §5 `noexcept` treatment (it is the Part's own mechanism,
the split-out page owns only the unwinding machinery); `limitations.md`'s
ELF-only summary (a registered summary of `elf-symbol-filtering`); the
trio's own cross-links and banner (the three-page design is intentional);
Part 7's external further-reading list (the only place the series cites
its sources).

---

## E. Reading paths by role, as ladder subsequences

The hub's role table is rendered from A1's `paths:` (A2), so each path
is a walk *up* the ladder by construction and A2c checks it.
A path may skip tiers but never steps back to a lower one, and within a
tier it follows member order; a path that continues into the Concepts
sequence does so only after its educational tiers are done, since the
two sequences are ordered independently (A1). It never jumps to a page
the ladder does not reach.

| Role | Path (tier · page) |
|---|---|
| New C/C++ library author | 0 · Five minutes → 0 · How a break shows up → 1 · Part 0 → 1 · Part 1 → 2 · Part 2 → 2 · Part 3 → 6 · Part 7 |
| C++ library maintainer | 1 · Part 1 → 2 · Part 4 (+ class layout branch) → 2 · Part 6 → 6 · Part 7 → 7 · Template-heavy libraries |
| CI / release engineer | 0 · How a break shows up → 3 · Compatibility Direction → 4 · Detecting Breaks → 5 · Where in the pipeline → 5 · Report the surface → 5 · Rollout and governance → then the Concepts sequence from c1 · Verdicts |
| Distribution / package maintainer | 2 · Part 5 → 7 · Products, not libraries → 7 · System libraries → 7 · Dependency floors → 7 · Packages and consumers |
| Product / SDK owner (several binaries) | 1 · Part 0 → 3 · Consumer models → 5 · Where in the pipeline → 7 · Products, not libraries → 7 · Template-heavy libraries |
| Plugin / SDK author | 2 · Part 2 → 3 · Compatibility Direction → 3 · Consumer models → 5 · Rollout and governance → 6 · Part 7 |
| AI agent / automated reviewer | 0 · How a break shows up → 5 · Triage a suspicious finding → then the Concepts sequence: c1 · Verdicts → c2 · Evidence & Detectability → (tool track) `use/output-formats.md` |

The last column of each row on the hub links the tool-track page the role
needs *after* the ladder (`start/choose-your-workflow.md` for most; the
output-formats page for agents) — the entry's `after:` — so the hand-off
to `use/` is one deliberate step, not an interleaving.

---

## F. Sequencing into pull requests

Each PR is independently mergeable and leaves every gate green. Gate
command for all of them: `python scripts/verify.py --profile pr --only
docs-contract,docs-build,ai-readiness`, plus `learning-ladder` (the A2
drift step) from P1 on. The "Plan phase" column maps each PR to the
plan's §6; where the two orders differ, this table wins, since it records
the dependencies between artifacts.

| PR | Plan phase | Carries | Depends on |
|---|---|---|---|
| P1 | 1 | A1 file (today's pages only; `paths:` for today's rows — each later PR appends its own page), A2 generator + tests, A2b nav-order check, A2c rules, A2d `validation:` block; A4's `level:` on the 16 blank pages and the reconciled values; the three within-group nav reorders (A2b); A3 footers on all 26 deep dives + 3 orientation pages and the four Part "Next" rewrites (A3); C11; front-door links (`docs/index.md`, `start/getting-started.md`); the `GENERATED_FILE_MARKERS`, `scripts/CLAUDE.md` and `verify.py` wiring. One PR, because the rules and the levels they read must land together; the hub gains only the two sentinel blocks | — |
| P2 | 1 | A7 hub rebuild (minus the "Now run it" table), anchor rewrites, A6 terminology term, C7 | P1 |
| P3 | 2 | C2, C3 (class-layout ownership), C5, C6, C8, C9, C10, and only the A5 fragments whose pages exist by then (`class-layout`; the `evidence-model` and `ast-frontend-resolution` additions for `08-detection.md` and `build-source-data.md`). Every other fragment, the `bundle-analysis` re-registration included, ships with the PR that creates its page (P4–P8) | P2 |
| P4 | 3 | B1 How a break shows up | P2 |
| P5 | 3 | B2 Where in the pipeline (moves the hub's "Now run it" table), B3 Report the surface | P4 |
| P6 | 3 | B4 Rollout and governance, B5 Triage a suspicious finding | P5 |
| P7 | 3 | B6 Products, not libraries (with the `bundle-analysis` re-registration), B7 Template-heavy libraries | P3 |
| P8 | 3 | B8 How system libraries stay compatible, B9 Packages and consumers, C13 | P7 |
| P9 | 4 | C1, C4, C12, C14, C15, C16 (worked examples and commands on existing pages) | P3, P5, P7 (C4 links B2 and B6, so both must exist for the strict build) |

P4–P9 can proceed in parallel once their dependency has merged; P9 is
the only one whose entries are each separable into their own smaller PR
if review load requires it. Each new page also appends itself to A1 and
its `paths:` rows in the same PR, since A2c's completeness rule fails
otherwise. No PR links a page a later PR creates: footers and in-page
pointers name the then-current neighbour (A3), and the PR that creates
the missing page rewrites them — so P5's B3 footer ends at Part 7, P7's
B7 footer at `dependency-floors.md`, and B2 §7 gains its B6 link in P7.
