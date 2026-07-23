# ADR-049: Documentation Operational Model (Ownership Registry + Docs-Contract Gate)

**Date:** 2026-07-22

**Status:** Accepted — Stage 1 (governance) implemented. Stages 2-5 below are
explicitly deferred, not silently dropped — see "Rollout stages" for what
each covers and why it isn't in scope here.

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

6. **`docs/development/documentation.md`** — the human-readable companion:
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
| 2. Source-of-truth automation | Generated CLI reference, Action inputs/outputs, MCP tools, Python API, config keys, platform/capability matrix | **Partial** — `scripts/gen_action_reference.py` (Action inputs/outputs from `action.yml`) proves the pattern; the rest are not built |
| 3. High-duplication cluster consolidation | Getting Started/Choose Workflow, evidence/scan/tool-modes, source-facts/build-evidence, verdict/policy/severity/exit-codes, baseline, GitHub Action, specialized contracts | **Partial** — exit-codes/severity/platform-support-matrix dedup, `getting-started.md` and `tool-modes.md` trims, ADR nav relaxation (see below), and `baseline-management.md`'s 3-way split (lifecycle concept / `create-baseline.md` how-to / `baseline-storage.md` recipes) landed; the GitHub Action page cluster and Specialized Checks regrouping did not |
| 4. Physical restructuring (`start/`/`learn/`/`use/`/`reference/`/`contribute/` + redirects) | **Not attempted** — high blast radius on live, indexed doc URLs; needs its own scoped pass with a redirect map, not a drive-by alongside governance work |
| 5. Case Library / future providers (Cython, NumPy, wheel) | **Not attempted** — no such providers exist yet to catalog |

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
- `docs/_meta/` (topics/terminology registries) and `docs/development/adr/`
  (individual ADRs) are both excluded from certain nav-coverage
  expectations by design — `_meta/` because mkdocs never builds it,
  individual ADRs because of the nav relaxation above — both documented in
  `docs/AGENTS.md` and this ADR respectively, not left as tribal knowledge.
- Physical restructuring (Stage 4) remains an open, larger decision. If it
  happens, it should get its own ADR (or amend this one) rather than being
  folded into an unrelated change, given the redirect-map and stale-URL
  risk the originating design document itself flagged.

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
track vs. tool track" split (`concepts/abi-api-handling.md`'s "Learning
Series" framing) is a separate, already-implemented decision this ADR does
not revisit or fold in.

## References

- `docs/AGENTS.md` — the machine-oriented contract this ADR's Decision
  summarizes.
- `docs/development/documentation.md` — the human-readable companion.
- `docs/_meta/topics.yaml`, `docs/_meta/terminology.yaml` — the registries.
- `scripts/check_docs_contract.py` — the gate implementation.
- PR #619 — the branch this ADR was written from; its review history
  documents the specific link-detection and validation gaps found and
  closed while building the gate.
