# ADR-051: Documentation Operational Model (Ownership Registry + Docs-Contract Gate)

**Date:** 2026-07-22

**Status:** Accepted — Stage 1 (governance), Stage 2 (source-of-truth
automation), Stage 3 (cluster consolidation), and Stage 4 (physical
restructuring) implemented; Stage 5 explicitly deferred, not silently
dropped — see "Rollout stages" for what each covers and why.

**Verified:** main@2e43d53 on 2026-08-04
**Decision maker:** (pending — recorded per repository convention, the same
bar ADR-044 D1 and ADR-048 set for a PR-driven ADR with no separate approval
step yet.)

---

## Context

`docs/` had accumulated the same kind of drift this repo's code already
guards against with `repo_facts.json` (CLAUDE.md "M1-4") and the CLI
contract (ADR-037): the same fact — the L0-L4 evidence-tier table, the
verdict/exit-code mapping, the platform-support matrix — restated by hand on
more than one page, with no mechanism to catch the second copy drifting from
the first. `docs/CLAUDE.md` (before this ADR) already documented a
three-page split for the evidence model (model / worked example / flag
reference) as a *convention*, but nothing checked that a new page didn't
silently duplicate it, and nothing recorded *who* was allowed to summarize
which topic where.

The proposal that triggered this work (a long design document proposing a
full documentation restructuring: physical directory reorganization into
`start/`/`learn/`/`use/`/`reference/`/`contribute/`, a terminology registry,
source-of-truth generators for every volatile surface, and a much richer
duplicate-content checker) explicitly recommended against attempting all of
that in one PR: *"the first practical PR here should not be moving dozens of
pages, but introducing `docs/AGENTS.md`, `topics.yaml`, metadata, and a
warning-only docs-contract."* This ADR records that first PR's scope as the
accepted decision, and the staged plan for what comes after it.

## Decision

Adopt a **one-fact-one-owner** documentation model, made machine-checkable
rather than left as prose convention:

1. **Four ownership kinds**, distinguished explicitly instead of conflated:
   *fact owner* (the code/schema/registry holding the exact value),
   *narrative owner* (the one page explaining a topic in full),
   *task owner* (the one page describing one practical workflow), and
   *view owner* (a generated table, a short summary, a case-index row —
   anything that re-shows a fact without re-deriving it).

2. **`docs/_meta/topics.yaml`** — a topic-ownership registry. Each topic
   names its `canonical_page` (narrative owner, required and unique per
   topic), optional `worked_example`/`reference_page`/`task_pages`/
   `allowed_summaries` (the pages permitted to reference it), and
   `fact_sources` (the code paths that hold the real values). Pilot scope:
   the six topics that already had an explicit split documented in prose
   (evidence-model, verdicts, baseline-lifecycle, public-surface,
   change-kinds, platform-support-matrix) — not a repo-wide sweep.

3. **`docs/_meta/terminology.yaml`** — the same ownership idea at the level
   of individual terms (ABI, API, ChangeKind, Verdict, …) rather than whole
   topics. A term's `canonical_page` need not be unique the way a topic's
   is (ABI and API share one page); what's tracked is where the term is
   *defined*, so a second, independently-drifting definition elsewhere can
   be flagged.

4. **Page front matter** (`doc_type`, `audience`, `level`, `canonical_for`,
   `summarizes`, `depends_on`, `lifecycle`, `generated`) — optional,
   incrementally rolled out to the pages the pilot registries reference,
   not required repo-wide yet.

5. **`scripts/check_docs_contract.py`** — the gate, wired into
   `scripts/verify.py --profile pr` and the `ai-readiness` CI job (CLAUDE.md
   "M0-3"). Two tiers:
   - **ERROR** (structural, deterministic): every registry-referenced path
     exists; no two topics share a `canonical_page`; `canonical_for`/
     `summarizes` round-trip against the registry; a `summarizes` claim
     requires an actual Markdown backlink to the topic's `canonical_page`
     (inline, reference-style, or fenced/inline-code-stripped correctly —
     several of these link-detection edge cases were only found and closed
     during PR #619 review, see that PR's history for the specific gaps);
     a `canonical_page` can't be `generated: true`; terminology entries are
     well-typed.
   - **WARN** (advisory, not a structural conflict): a `canonical_page`
     with no front matter yet; an identical 40+-word prose block or
     10+-word table verbatim in two or more manual pages; a page appearing
     to redefine a registered term itself instead of linking to its
     `canonical_page`.

   Deliberately warning-only for duplication: semantic duplicate detection
   is unreliable enough that blocking PRs on it would train authors to work
   around the check rather than fix real drift. Ownership violations are
   ERROR because they're exact and deterministic (a path either exists or
   it doesn't; two topics either share a `canonical_page` or they don't).

6. **`docs/contribute/documentation.md`** — the human-readable companion:
   why the model matters, one template per page shape (tutorial/how-to/
   explanation/reference/hub/migration/case), three real before/after
   duplication fixes from this branch as worked examples, the `lifecycle`
   field's three states, a page-retirement procedure, and a PR checklist.
   `docs/AGENTS.md` is the machine-oriented contract; this page is the
   "why and how to do it well" companion — the same split CLAUDE.md/AGENTS.md
   already models at the repo root (CLAUDE.md "M1-1").

## Rollout stages

The originating design document proposed five stages. This ADR's decision
is Stage 1 plus a handful of Stage 2/3 items that turned out to be small,
bounded, and safe to land alongside it. Stages 4 and 5 are explicitly not
attempted here:

| Stage | Scope | Status |
|---|---|---|
| 1. Governance | `topics.yaml`, `docs/AGENTS.md`, front matter, warning-only docs-contract | **Done** |
| 2. Source-of-truth automation | Generated CLI reference, Action inputs/outputs, MCP tools, Python API, config keys, platform/capability matrix | **Done** — all 6 built, each following the same pattern (a generator + a `--check` mode + a mirrored pytest test, per `docs/AGENTS.md`'s "Regenerating generated docs"): `scripts/gen_action_reference.py` (Action inputs/outputs), `scripts/gen_cli_reference.py` (every command/option, from the live Click tree), `scripts/gen_mcp_reference.py` (every `@mcp.tool()` parameter, from `abicheck/mcp_server.py`'s signatures/docstrings — requires the `mcp` extra), `scripts/gen_python_api_reference.py` (every `abicheck.service.__all__` signature/dataclass), `scripts/gen_config_reference.py` (`.abicheck.yml` key/type list, from `BuildConfig`'s strict-schema registries), and `scripts/gen_platform_matrix.py` (the host-OS × binary-format capability matrix, sourced from the new `scripts/platform_capabilities.py` — see "Stage 2: platform/capability matrix" below). |
| 3. High-duplication cluster consolidation | Getting Started/Choose Workflow, evidence/scan/tool-modes, source-facts/build-evidence, verdict/policy/severity/exit-codes, baseline, GitHub Action, specialized contracts | **Done** — exit-codes/severity/platform-support-matrix dedup, `getting-started.md` and `tool-modes.md` trims, ADR nav relaxation (see below), `baseline-management.md`'s 3-way split (lifecycle concept / `create-baseline.md` how-to / `baseline-storage.md` recipes), the source-facts/build-evidence cluster (new `source-evidence-producers` topic; `producing-source-facts.md` as the canonical decision/wrapper-injection guide, `build-evidence-setup.md` as its `reference_page` owning the Clang-plugin build/wiring/traps and project-contract detail, with the two basic-invocation duplicates on each page trimmed to a cross-link), the GitHub Action page cluster (nested under one `mkdocs.yml` nav group instead of three flat "GitHub Action: ..." entries — nav-only, no file moves/redirects), and the Specialised Checks regrouping (11 flat entries regrouped into 7 contract-surface sub-groups — Packages & Multi-Library Products, Applications & Consumers, Plugins & Dynamic Loading, Python Extensions, Kernel & eBPF, Build & Toolchain Contracts, Security & Deployment — again nav-only) all landed. |
| 4. Physical restructuring (`start/`/`learn/`/`use/`/`reference/`/`contribute/` + redirects) | High blast radius on live, indexed doc URLs; needs its own scoped pass with a redirect map, not a drive-by alongside governance work | **Done** — see "Stage 4: physical restructuring" below |
| 5. Case Library / future providers (Cython, NumPy, wheel) | No such providers exist yet to catalog | **Not attempted** |

### Stage 2: platform/capability matrix

The other five Stage 2 generators all wired a generator onto a schema that
already existed for another reason (`action.yml`, the live Click tree, the
`@mcp.tool()` signatures, `service.__all__`, `BuildConfig`'s registries). The
platform/capability matrix had no such schema to wire into — "what symbol/type
diff works on which host for which binary format" is a fact about the tool's
actual behavior, not something derivable from `docs/contribute/usecase-
registry.yaml` (a per-use-case coverage registry, not this matrix) or a CI
workflow matrix (which records what's *validated*, not what's *capable*).

Closed by introducing the missing piece: **`scripts/platform_capabilities.py`**,
a small, hand-curated, pure-Python data module (the same "pure stdlib,
importable" shape as `scripts/evidence_tiers.py`) recording, per binary format
(ELF/PE/Mach-O), each host OS's symbol-diff and type/param-diff capability and
required tooling. **`scripts/gen_platform_matrix.py`** renders it into
`docs/reference/platforms.md`'s "Quick Reference: What Works Where" section,
spliced between `<!-- BEGIN/END GENERATED: platform-matrix -->` sentinels —
the same splice-into-a-hand-authored-file pattern `gen_examples_docs.py`
already uses for `examples/README.md`'s generated regions, since the rest of
`platforms.md` (validation status, dependency summary, Windows toolchain
matrix, macOS ARM64 differences, known limitations) stays hand-authored
narrative rather than becoming a second, larger generated file. Regenerating
also normalized a pre-existing inconsistency in the hand-typed tables (the
native-host row inconsistently read "✅ Yes" instead of "✅ Full" on two of the
three tables) — exactly the kind of small drift a generator exists to prevent
from recurring.

### Stage 4: physical restructuring

Executed as one scripted, fully-verified migration rather than a hand-rolled
series of moves, given the scale: 402 files under `docs/`, 3,027 internal
Markdown links across 331 files. The taxonomy each old top-level directory
maps to:

| Old | New | Rationale |
|---|---|---|
| `user-guide/` | `use/` | Task-oriented how-to content — unchanged in kind, renamed for a shorter, verb-first top-level name consistent with the other four. |
| `concepts/` (incl. the ABI/API Handling educational track) | `learn/` | The narrative/conceptual track — mental models, not step-by-step tasks. |
| `reference/` | `reference/` (unchanged) | Curated + generated exhaustive reference already had the right name. |
| `examples/` | `reference/examples/` | Per-case docs are a reference namespace (exhaustive, looked-up rather than read start-to-end) — nested under `reference/` instead of a sibling top-level directory. |
| `schemas/` | `reference/schemas/` | Same reasoning as `examples/` — a published, versioned reference artifact set. |
| `development/` (incl. `development/adr/`) | `contribute/` (incl. `contribute/adr/`) | Contributor- and governance-facing material — ADRs, plans, parity status, the use-case registry, the archive. |
| top-level onboarding pages (`getting-started.md`, the worked real-world example) | `start/` | A genuinely new top-level landing category for first-contact material, not a rename of an existing directory. |
| `_meta/` | `_meta/` (unchanged) | Machine-only registries, never published; no reason to move. |

Mechanics:

- **File moves**: 375 files moved via `git mv` (of the 402 total under
  `docs/`; the remainder — `_meta/`, already-correctly-placed files, and
  `index.md`/`AGENTS.md`/`CLAUDE.md` at the docs root — didn't move).
- **Link rewriting**: every internal Markdown link was resolved relative to
  its *old* source file's location, looked up in the old→new path mapping,
  and recomputed relative to the *new* source file's location via
  `posixpath.relpath` — not a blind string substitution, since a same-
  subtree move needs no link change while a cross-directory move (e.g.
  `examples/` → `reference/examples/`, one level deeper) needs an extra
  `../` segment. Beyond `.md` content itself, this also required fixing
  relative-link **string literals embedded in Python source** — the
  `gen_*.py` doc generators (`gen_examples_docs.py`, `gen_detector_spec.py`,
  `gen_cli_reference.py`, `gen_action_reference.py`, `gen_mcp_reference.py`,
  `gen_python_api_reference.py`) each produce `.md` content from hard-coded
  path constants and link strings in their own source, which a docs-only
  content sweep cannot see.
- **Redirects**: `mkdocs.yml`'s `redirect_maps` (via the already-installed
  `mkdocs-redirects` plugin, extended rather than introduced — it already
  carried 3 entries from Stage 3) grew to 373 entries, one per moved and
  rendered `.md` page, so every previously published/indexed URL still
  resolves. 5 moved-but-non-rendered paths (1 YAML registry, 4 JSON schemas)
  were deliberately excluded — `mkdocs-redirects` only redirects rendered
  pages, and non-`.md` files aren't served through mkdocs' page router
  anyway.
- **Path-constant and registry fixes**: every hard-coded `docs/<old-dir>`
  path constant across `scripts/*.py` and `tests/*.py` (AI-readiness checks,
  schema-publishing scripts, use-case-registry/scenario sync checks,
  generated-doc tests), plus `docs/_meta/topics.yaml`/`terminology.yaml`'s
  path fields, were updated to match the new layout.
- **Verification**: `mkdocs build --strict` (link validity), the full fast
  test suite, `scripts/check_ai_readiness.py`, and `scripts/check_docs_contract.py`
  all pass clean against the new layout.

The redirect-map and stale-URL risk this ADR's Consequences section
originally flagged as the reason Stage 4 needed its own pass is addressed by
the 373-entry `redirect_maps` table above — an external link to any
pre-Stage-4 URL 30x-redirects to the file's new location instead of 404ing.

One additional, un-staged change rides along in this same decision: the
`adr-index-nav-sync` AI-readiness check originally required every ADR
individually in `mkdocs.yml`'s nav (on top of being linked from
`adr/index.md`) — a rule this ADR itself would have had to satisfy by adding
a 49th flat nav entry to an already 48-entry list. Relaxed to: every ADR
must be linked from the index, and the *index page* must be in nav (which is
what actually makes every ADR reachable from published navigation); added in
exchange, every ADR must carry Status metadata, and a Superseded ADR must
link to the ADR that replaced it (checked by target-filename shape, not just
"any link exists" — see PR #619 review history).

## Consequences

- A future topic (Cython/NumPy/wheel providers, a new evidence tier, a new
  CLI mode) that needs cross-page ownership discipline has a registry
  pattern and a gate to extend, instead of another ad-hoc prose convention.
- The gate only covers the pilot topic/term set. Extending coverage to the
  rest of `docs/` is intentionally incremental (docs/AGENTS.md's "Rollout
  status") — a page outside the pilot set can still silently duplicate
  content today. This is accepted, not a bug: the alternative (requiring
  front matter and registry entries repo-wide immediately) would have
  forced a much larger, riskier PR against the "first PR should be
  governance only" recommendation this ADR is built on.
- `docs/_meta/` (topics/terminology registries) and `docs/contribute/adr/`
  (individual ADRs) are both excluded from certain nav-coverage
  expectations by design — `_meta/` because mkdocs never builds it,
  individual ADRs because of the nav relaxation above — both documented in
  `docs/AGENTS.md` and this ADR respectively, not left as tribal knowledge.
- Physical restructuring (Stage 4) is now done (see "Stage 4: physical
  restructuring" above), amending this ADR rather than a new one, per the
  original plan recorded here. `docs/AGENTS.md`'s "Layout" section describes
  the resulting `start/`/`learn/`/`use/`/`reference/`/`contribute/` taxonomy
  in full; this ADR records why each old directory mapped where it did and
  how the redirect/link-rewrite risk was retired.

## Alternatives considered

- **A single style-guide document instead of a machine-checked registry.**
  Rejected: this is exactly what `docs/CLAUDE.md` already was before this
  ADR, and it didn't prevent the duplication this ADR responds to — an
  unenforced convention degrades the same way `CHANGELOG.md`'s
  `[Unreleased]` section did before `changelog.d/` fragments existed.
- **Blocking (ERROR-level) duplicate-content detection from day one.**
  Rejected: text-similarity duplicate detection has real false-positive
  risk (templated case pages, legitimately repeated short phrases); warning
  first, promote to blocking later once the corpus is clean, is the safer
  order — the same reasoning the design document itself gave.
- **Skipping the terminology registry and only doing topic ownership.**
  Considered, since topics.yaml alone was the design document's explicit
  minimum. Added anyway because it was small, low-risk, and the same
  ownership idea at a finer grain — not a scope expansion in the sense
  Stage 2-5 would be.
- **Doing the physical restructuring alongside governance in one PR.**
  Rejected per the design document's own recommendation and the size/risk
  of getting redirects wrong across dozens of already-published, indexed
  URLs — see Stage 4 above.

## Relationship to existing conventions

This ADR is the docs-specific instance of a pattern already established
elsewhere in the repo: `repo_facts.json` (CLAUDE.md "M1-4") is the same
single-source-of-truth idea for volatile repository facts; ADR-037 (CLI
Interface Contract) is the same "gate a surface against silent drift" idea
for the CLI; `changelog.d/` fragments are the same "stop hand-editing a
shared section that always conflicts" idea for the changelog. No existing
ADR covers documentation structure directly — the pre-existing "educational
track vs. tool track" split (`learn/abi-api-handling.md`'s "Learning
Series" framing) is a separate, already-implemented decision this ADR does
not revisit or fold in.

## References

- `docs/AGENTS.md` — the machine-oriented contract this ADR's Decision
  summarizes.
- `docs/contribute/documentation.md` — the human-readable companion.
- `docs/_meta/topics.yaml`, `docs/_meta/terminology.yaml` — the registries.
- `scripts/check_docs_contract.py` — the gate implementation.
- PR #619 — the branch this ADR was written from; its review history
  documents the specific link-detection and validation gaps found and
  closed while building the gate.
