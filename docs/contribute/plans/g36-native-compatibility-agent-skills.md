---
doc_type: contributor
level: advanced
lifecycle: active
---

# G36 — Native Compatibility Agent Skills: Design, Build, Publish

**ADR:** [ADR-058](../adr/058-native-compatibility-agent-skills.md)
**Type:** Initiative plan (multi-phase); no `usecase-registry.yaml` entries
— publishing an agent-skill distribution surface is a cross-cutting
product/tooling initiative, not a detector capability, so it is tracked here
rather than in the registry (consistent with how G19/G24/G30 track their
own initiative work).
**Effort:** L/XL (phased P0/P1/P2) · **Risk:** medium — the two required
product changes (`abicheck info`, a comparability reason-code field) are
small and additive; the real risk is scope creep in the skill portfolio and
silent drift between `skills-src/` and published skills, both of which this
plan's admission-bar and drift-test items exist to contain.

## Problem

ADR-058 decided the architecture: four P0 public skills
(`native-binary-compatibility-review`, `native-api-evolution`,
`native-release-compatibility`, `native-consumer-compatibility`), a
`skills-src/` → generated `.agents/skills/` publication model, CLI as the
normative execution backend, and a fixed set of safety invariants. This plan
is the sequenced backlog that gets there — what files, what tests, what
documentation, in what order, with what's genuinely deferred to P1/P2 and
why.

## Sequencing principle

Each phase should land as multiple small, independently reviewable PRs, not
one PR per phase — the same discipline G30 established for the GitHub
Actions rollout. No PR should combine a product-surface change (e.g.
`abicheck info`) with skill-content authoring; those have different review
failure modes (a product PR needs `mypy`/coverage/changelog fragment
discipline; a skill-content PR needs trigger/behavioral eval review). Every
item below states whether it requires a user-facing CLI/API change so
reviewers can route it to the right review lens immediately.

---

## P0 — Architecture and first useful release

### P0.1 — `skills-src/` source tree and shared Layer-B fragments — **not started**

**Problem:** Nothing exists yet. Building four skills' worth of `SKILL.md`
prose before the shared domain-knowledge fragments they all cite exist
guarantees the fragments get written four times, slightly differently, and
drift immediately — exactly what ADR-058's "one fact, one place" principle
exists to prevent.

**Change:** Create `skills-src/shared/` with one Markdown fragment per
domain concept named in ADR-058's Layer B list:
`compatibility-contracts.md` (ABI vs. source-API vs. runtime compatibility
as three distinct questions), `evidence-and-depth.md` (L0–L5 ladder, what
each layer buys — summarizing, not duplicating,
`docs/learn/evidence-and-detectability.md`), `baseline-and-comparability.md`
(baseline selection + ADR-050's comparability contract, in skill-actionable
terms), `public-surface-and-scoping.md`, `compiler-and-build-profiles.md`,
`consumer-scoping.md` (ADR-057's consumer graph, `--used-by`/
`--required-symbol(s)`), `policies-and-suppressions.md`,
`report-interpretation.md` (how to read a JSON report's verdict/gate/
contract-coverage/finding blocks), `root-cause-grouping.md`,
`remediation-catalog.md` (the pattern catalog ADR-058's
`native-api-evolution` skill draws from: pImpl/opaque handles, compatibility
wrappers, overloads over destructive signature changes, reserved
fields/slots, versioned interfaces, plugin capability negotiation,
deprecation/migration lifecycle), and `safety-invariants.md` (verbatim
source for ADR-058's eleven numbered invariants — every `SKILL.md` links
this file rather than restating it). Each fragment states, at its top, which
canonical `docs/learn/`/`docs/use/`/`docs/reference/` page(s) it summarizes
(mirroring `docs/AGENTS.md`'s `summarizes:` front-matter field convention),
so a docs-contract-style check (P0.6) can verify the summary hasn't drifted
from its source.

**Files:** `skills-src/shared/*.md` (11 new files);
`skills-src/CLAUDE.md` (required — this is a new major sub-tree per
`scripts/check_ai_readiness.py`'s `claude-md-coverage` check once it's added
to `REQUIRED_CLAUDE_MD_DIRS`, see P0.6).

**Tests:** none yet (structural tests land in P0.5); a manual editorial pass
confirming each fragment is self-contained prose (no unresolved
cross-fragment references) before P0.2 starts consuming them.

**Dependencies:** none — this is the first item.

**PR boundary:** one PR per fragment cluster is unnecessary; land all
eleven fragments in one PR since they have no independent review value
split apart, but keep this PR free of any `SKILL.md`/generator code.

---

### P0.2 — Author the four P0 `SKILL.md` files — **not started**

**Problem:** The actual skill content — the part a user's agent reads.

**Change:** Write `skills-src/<skill-name>/SKILL.md` for each of the four
skills, each with:
- `name`/`description` frontmatter written to trigger on the real-world
  request phrasings ADR-058's Product-positioning section names for that
  skill (e.g. `native-binary-compatibility-review`'s description must cover
  "review this PR/branch/commit for binary compatibility," "will this break
  existing consumers," and "why did this suddenly report dozens of
  breaks").
- A workflow/decision tree specific to that skill's job (per ADR-058's
  Decision → Public skill taxonomy table), written to call out to
  `skills-src/shared/*.md` fragments by reference wherever the content is
  shared, never inlining a paraphrase of a fragment.
- A `references/` subdirectory holding only genuinely skill-specific
  material (e.g. `native-release-compatibility/references/soname-and-semver.md`
  is specific to that skill; it does not belong in `shared/`).
- Termination criteria per ADR-058's Skill content model (when the job is
  actually done — e.g. `native-api-evolution` ends by invoking the
  equivalent of `native-binary-compatibility-review`'s verification step on
  the proposed change, not by asserting the design is safe).

Each skill's CLI workflow is grounded against the *current* CLI surface
confirmed in this plan's grounding pass (`compare` with `--used-by`/
`--required-symbol(s)`/`--contract`/`--policy`/severity flags; `scan`;
`project validate`/`project plan`; `aggregate`) — no skill may reference a
flag, command, or report field that doesn't exist today (checked
mechanically by P0.7).

**Files:** `skills-src/native-binary-compatibility-review/SKILL.md` (+
`references/`), `skills-src/native-api-evolution/SKILL.md` (+
`references/design-patterns.md`), `skills-src/native-release-compatibility/
SKILL.md` (+ `references/soname-and-semver.md`), `skills-src/
native-consumer-compatibility/SKILL.md` (+
`references/application-vs-plugin-branches.md`).

**Tests:** trigger tests land in P0.8; this item is content authoring only.

**Docs:** none directly — the skill catalog page (P0.9) links to these
once generated.

**Dependencies:** P0.1 (shared fragments must exist to reference).

**PR boundary:** one PR per skill (four PRs) — each is independently
reviewable for its own decision-tree correctness, and a reviewer for
`native-release-compatibility` doesn't need to also review
`native-api-evolution`'s design-pattern catalog in the same pass.

---

### P0.3 — Generator: `skills-src/` → `.agents/skills/` — **not started**

**Problem:** ADR-058 requires generated, self-contained output — no
symlinks, no hand-maintained copies — and requires each skill's
`references/` manifest to resolve only the `shared/` fragments that skill
actually cites.

**Change:** `scripts/gen_agent_skills.py` — for each `skills-src/<name>/`
directory: (1) parse its `SKILL.md` for a references-manifest comment or
explicit link list identifying which `shared/*.md` fragments it uses; (2)
copy the skill's own `SKILL.md` and `references/` verbatim into
`.agents/skills/<name>/`; (3) copy (not symlink) each cited `shared/*.md`
fragment into `.agents/skills/<name>/references/shared/`, rewriting the
skill's own internal links to the copied path; (4) fail loudly if a
`SKILL.md` references a `shared/` fragment that doesn't exist, or a
`shared/` fragment that exists but is never cited by any skill (dead
fragment — same "no orphaned content" discipline as
`changekind-detector`'s WARN check for orphaned `ChangeKind`s). Modeled
directly on the existing `scripts/gen_cli_reference.py`/
`scripts/gen_mcp_reference.py` pattern (`docs/AGENTS.md`'s "regenerating
generated docs" contract) — same idempotency and same "verify.py fails on
drift" requirement, applied to a new artifact family.

**Files:** `scripts/gen_agent_skills.py` (new); `.agents/skills/**`
(generated output, committed); `scripts/verify.py` (new step,
`agent-skills-generated`, wired into the `pr` profile alongside the
existing generated-doc regeneration checks).

**Tests:** `tests/test_gen_agent_skills.py` — idempotency (running the
generator twice produces identical output), dead-fragment detection,
missing-fragment-reference detection, no-symlinks-in-output assertion,
every generated `SKILL.md`'s internal links resolve inside its own
installed directory (the self-containment invariant, checked mechanically,
not just by design intent).

**Dependencies:** P0.1, P0.2.

**PR boundary:** one PR — the generator and its first generated output
land together, since an ungenerated generator has no reviewable output.

---

### P0.4 — `abicheck info --format json` — **not started**

**CLI/API change:** yes — new root-level command.

**Problem:** No machine-readable way for a skill (or any agent) to ask
"what can this installed abicheck do" — confirmed absent from
`abicheck/cli*.py` in this plan's grounding pass. A skill deciding whether
`--contract` is available, or which extraction providers exist on this
host, otherwise has to parse `--version`'s human string or probe by
trial-and-error, which both `native-release-compatibility` and
`native-binary-compatibility-review`'s tool-selection steps need to avoid.

**Change:** Add `abicheck info` (small, read-only, no operands) emitting
JSON: `abicheck_version`, `report_schema_version`, `snapshot_schema_version`,
`scan_schema_version` (reading the same schema-registry lookup ADR-055 D3
introduced, not re-deriving version numbers independently), the current
root command list (so a skill can self-check "does my target support
`project`"), available extraction providers (castxml/clang presence,
detected on the host the same way existing dumper-provider auto-detection
already probes), and platform capabilities (ELF/PE/Mach-O support — all
three ship unconditionally today, but this keeps the field meaningful if
that ever changes). Checked against `AGENTS.md`'s "Adding a new top-level
command" admission bar (ADR-054 D6) — see that section for the exact
wording. Five of the six criteria are unambiguous; criterion 2 ("its
operand is a domain object a user already thinks in terms of") is the one
genuinely debatable call, since `info` takes no operand at all. The
precedent this leans on is `--version` — already an existing zero-operand
top-level surface, whose "operand" is likewise the installation itself, not
a binary/report/config. `info` is that same case promoted from a flag to a
proper structured-output command because a skill needs to parse it
reliably. This reading should be treated as needing explicit maintainer
sign-off when P0.4 actually lands, not as a foregone conclusion — flag it
for discussion in that PR rather than asserting the bar is mechanically
cleared. Lands with `tests/test_cli_root_surface.py` + `AGENTS.md` +
`README.md` + generated CLI reference updated in the same PR per the bar's
own sixth criterion.

**Files:** `abicheck/cli_info.py` (new, sibling module per the "Adding a
new top-level command" convention in `AGENTS.md`); `abicheck/cli.py` (side
effect import registration); `tests/test_cli_root_surface.py` (extend the
pinned root-command-set assertion to include `info`); `README.md`
("Which command do I need?" table gets one more row);
`docs/reference/cli-reference.md` (regenerated).

**Tests:** `tests/test_cli_info.py` — JSON shape, schema-version values
match `serialization.SCHEMA_VERSION`/the report-schema constant at import
time (no hand-copied duplicate numbers), provider-detection reflects the
actual test-environment tool availability.

**Docs:** `README.md`, `docs/reference/cli-reference.md` (generated),
changelog fragment (`changelog.d/`, `### Added`).

**Dependencies:** none — independent of the skill-authoring track, can land
in parallel with P0.1–P0.3.

**PR boundary:** one PR, product-surface only — no skill-content changes
bundled in.

---

### P0.5 — Finer-grained `reason.codes` on the existing not-comparable object — **not started**

**CLI/API change:** yes — additive report-schema field.

**Problem:** A `not_comparable` result already renders as a top-level
`"reason": {"kind": ..., "message": ...}` object in the `--format json`
document (schema 2.17, `cli_compare_helpers._report_not_comparable`), but
`kind` today is only ever `"profile_mismatch"` or `"scope_mismatch"` — two
coarse buckets. The *specific* mismatched field(s) (compiler family vs.
compiler version vs. language standard vs. ...) are only recoverable from
the free-text `message`, so a skill (or any caller) branching on the real
cause has to parse prose. `native-binary-compatibility-review`'s "establish
comparability" workflow step and `native-release-compatibility`'s
"baseline comparability" step both need a typed reason instead.

**Change:** Add a `codes` field — an **array**, not a singular `code` — on
that same `reason` object (no new top-level block — `comparability` does
not exist in the current schema and this does not invent one). An array is
required, not a convenience: `check_contracts_comparable` raises one
exception whose message can already list *multiple* simultaneously
mismatched fields (e.g. `compiler_family` and `abi_dialect` differing in
the same comparison), and a singular code would force discarding all but
one cause or inventing an arbitrary precedence order — a skill that fixes
the reported cause and re-runs would then fail again on the silently
omitted one. Values are drawn from a documented, closed enum covering every
field `comparability.py`'s `PROFILE_FIELD_KEYS` (`compiler_family`,
`compiler_version`, `abi_dialect`, `language_standard`, `target_triple`,
`pointer_width`, `endianness`, `macro_ops`, `pass_through_flags`,
`include_sequence`, `header_sequence`), `_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`'s
DPC++-only addition (`frontend_context_kind` — its own dedicated code, not
folded into the generic fallback, since G32 Phase D already makes it a
known, stable, independently-mismatching field), and `SCOPE_FIELD_KEYS`/
`_MANIFEST_SCOPE_FIELD_KEYS` (`headers`, `public_header_dirs`,
`translation_units`) can each independently contribute, plus one explicit
code per exception category already carved out in `comparability.py`
(`_PLATFORM_IDENTITY_FIELDS`, `_BUILD_CONTEXT_FIELDS`) and one
`other_profile_mismatch`/`other_scope_mismatch` catch-all for any field not
individually enumerated — so a future `PROFILE_FIELD_KEYS`/
`SCOPE_FIELD_KEYS` addition degrades to a generic-but-still-typed code
instead of silently emitting nothing. Derived from the same field-by-field
comparison `check_contracts_comparable`/`compute_extraction_contract`
already perform when raising `ProfileMismatchError`/`ScopeMismatchError` —
promotes existing internal evidence to a stable public field, adds no new
detection logic. Report-schema version bump per the existing
`REPORT_SCHEMA_VERSION` convention (ADR-047 §7 / ADR-055 D3's registry).

MCP's `abi_compare` tool needs its own, separate treatment, not a
copy-the-CLI-field assumption: its `not_comparable` envelope emits
`"reason"` as a bare **string** (`str(exc)`, `mcp_server.py`), not an
object — adding `codes` there cannot mean mutating `reason`'s type without
breaking existing MCP consumers. Add a new sibling field,
`reason_codes` (array, same enum, same derivation), alongside the existing
string `reason`, and leave `reason` itself untouched — this is an additive,
backward-compatible envelope change, not a type migration, and should be
implemented and tested as such.

**Files:** `abicheck/comparability.py` (`ProfileMismatchError`/
`ScopeMismatchError` carry the specific mismatched field(s) as structured
data, not just a rendered message, so `codes` can be derived without
re-parsing text — and so a multi-field mismatch keeps every field, not just
the first one hit), `abicheck/cli_compare_helpers.py`
(`_report_not_comparable` emits `codes`), `abicheck/cli_compare_release.py`
(its own `"reason": {...}` construction site gets the same field),
`abicheck/mcp_server.py` (`abi_compare`'s `{"status": "not_comparable",
"reason": ..., "reason_codes": [...]}` — additive sibling field, `reason`
unchanged), `abicheck/schemas/__init__.py` (schema version bump + field
registration), `docs/reference/change-kinds.md` or a new `docs/reference/
comparability-reason-codes.md` (document the closed enum — new page only
if it doesn't fit as a section of an existing comparability doc; check
`docs/use/contract-evaluation.md` and `docs/reference/
compatibility-evaluation-config.md` first per `docs/AGENTS.md`'s "extend an
existing canonical owner" rule before creating one).

**Tests:** `tests/test_comparability_gate.py` (extend with `codes`
assertions covering every `PROFILE_FIELD_KEYS`/`_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`/
`SCOPE_FIELD_KEYS` entry, each exception carve-out, and the fallback code
for an unrecognized field — one single-cause case each, **plus** at least
one dedicated multi-field-mismatch case asserting `codes` contains every
simultaneously-mismatched field, not just the two current coarse kinds and
not just single-cause cases); `tests/test_report_schema_receipt.py`-style
version-bump check if one exists for this schema family; a test asserting
`abi_compare`'s MCP `not_comparable` path emits `reason_codes` matching the
CLI's `codes` for the same mismatch (ADR-055 D4's "one shared
implementation" invariant, applied to this new field) while leaving its
existing string `reason` field's shape unchanged (an explicit
backward-compatibility regression test, not just a new-field test).

**Docs:** whichever comparability doc the field lands in, plus a
changelog fragment (`### Added`).

**Dependencies:** none — independent of P0.1–P0.4, can land in parallel.

**PR boundary:** one PR, product-surface only.

---

### P0.6 — AI-readiness gate coverage for the new tree — **not started**

**Problem:** `skills-src/` is a new major sub-tree; without registration it
silently falls outside `claude-md-coverage`, and `.agents/skills/` (fully
generated) needs an explicit "don't hand-edit" marker convention so a
contributor doesn't patch generated output directly the way
`generated-file-ownership` already prevents for `docs/reference/examples/
case*.md`.

**Change:** Add `skills-src/CLAUDE.md` (scoped context, not an `@AGENTS.md`
adapter, per this repo's own convention — see this file's own
`AGENTS.md`/`CLAUDE.md` split as the model); add `skills-src` to
`REQUIRED_CLAUDE_MD_DIRS` in `scripts/check_ai_readiness.py`; add a
"this file is generated by `scripts/gen_agent_skills.py` — do not hand-edit"
marker comment convention to every generated `.agents/skills/**/SKILL.md`
and register that marker pattern in `GENERATED_FILE_MARKERS` so
`generated-file-ownership` covers it the same way it covers every other
generated artifact family in this repo.

**Files:** `skills-src/CLAUDE.md` (new); `scripts/check_ai_readiness.py`
(`REQUIRED_CLAUDE_MD_DIRS`, `GENERATED_FILE_MARKERS`); `scripts/
gen_agent_skills.py` (emit the marker — depends on P0.3).

**Tests:** `python scripts/check_ai_readiness.py` passes with no new
errors; a regression test asserting `skills-src` is in the required-dirs
tuple.

**Dependencies:** P0.1 (the tree must exist), P0.3 (the generator emits the
marker).

**PR boundary:** small, can ride with P0.3's PR or land immediately after.

---

### P0.7 — Structural and tool/API drift tests — **not started**

**Problem:** ADR-058's Testing architecture commits to more than markdown
linting — a renamed CLI flag or removed report field referenced by a
skill's prose must fail CI, not silently rot.

**Change:**
- **Structural**: valid `SKILL.md` frontmatter (`name`/`description`
  present, length/charset constraints per the Agent Skills spec), every
  internal link inside a generated `.agents/skills/<name>/` tree resolves
  to a path inside that same tree (no path traversal to a sibling skill or
  to `skills-src/`), no broken links to `docs/` pages the skill cites.
- **Tool/API drift**: extract the current CLI command/option tree via the
  same `click`-introspection mechanism `scripts/gen_cli_reference.py`
  already uses, and every CLI invocation example inside a skill's
  `SKILL.md`/`references/` is checked against that live tree — a flag or
  command a skill's prose names but the CLI no longer has fails the test
  with the offending file:line. Same treatment for report-JSON field paths
  a skill's `report-interpretation.md` fragment names, checked against the
  live JSON-schema/dataclass definitions (`abicheck/schemas/__init__.py`,
  `checker_types.py`).

**Files:** `tests/test_agent_skills_structural.py` (new),
`tests/test_agent_skills_drift.py` (new); reuses
`scripts/gen_cli_reference.py`'s introspection helpers (extracted to a
shared importable function if not already, rather than re-implementing CLI
introspection a second time).

**Tests:** the two new test files themselves; wired into
`scripts/verify.py --profile pr` as a new step (`agent-skills-structural`,
`agent-skills-drift`).

**Dependencies:** P0.2 (skill content must exist to test against), P0.3
(generated output is what structural tests validate), P0.4 (the `info`
command's own surface should be drift-tested once it exists, though not a
hard blocker).

**PR boundary:** one PR per test file is fine, or combined — low risk
either way since both are additive test infrastructure.

---

### P0.8 — Trigger tests — **not started**

**Problem:** ADR-058's central product bet is that these skills trigger on
real user language, not abicheck vocabulary, and that they do *not*
false-trigger on adjacent-but-out-of-scope requests. This needs to be
tested, not assumed from having written a plausible-sounding `description`.

**Change:** A small labelled corpus of request strings — the positive set
drawn directly from ADR-058's Product-positioning section ("Review this PR
for binary compatibility," "Can I make this public C++ API change without
breaking old consumers," "Can we release this as a minor version," "Will
this old application work with the new library," "Why did this
compatibility check suddenly report dozens of breaks," "Will this binary
still work after moving to a new OS/container," "How do I keep ABI
compatibility across compiler/client profiles") each labelled with its
expected target skill (or, for the OS/container-upgrade and
compiler/client-profile phrasings — P1 candidates per ADR-058 — labelled as
"no P0 skill should exclusively claim this, but should not be silently
mishandled either" until those skills exist); the negative set (REST/OpenAPI
compatibility, database migrations, Java API compatibility, arbitrary JSON
schema compatibility) labelled "no `native-*` skill should trigger." Run
against each target agent's actual skill-matching behavior where that's
externally drivable (a scripted Claude Code / Codex session issuing the
request and asserting which skill activated); where an agent's internal
matching isn't scriptable, this degrades to a manual validation pass
recorded in P1.5's cross-agent validation log rather than an automated
gate for that agent specifically.

**Files:** `tests/agent_skills/trigger_corpus.yaml` (or similar — labelled
request/expected-skill pairs), `tests/test_agent_skills_triggers.py` (drives
whichever agents are scriptable in CI, e.g. Claude Code via its own
harness).

**Tests:** the corpus-driven test itself; CI-scriptable subset gated in
`pr` profile, the rest tracked as a manual checklist in P1.5.

**Dependencies:** P0.2, P0.3.

**PR boundary:** own PR, since this is evaluation infrastructure distinct
from skill content or the generator.

---

### P0.9 — Documentation: skill catalog and dogfooding — **not started**

**Problem:** ADR-058 commits to a small canonical docs section, not a
duplicate of existing ABI/API educational material, and to dogfooding
before any external publication step.

**Change:** One new `docs/use/agent-skills.md` page (`doc_type: how-to`,
per `docs/AGENTS.md`'s front-matter schema — `use` is not one of the eight
valid `doc_type` values; this is a task-oriented page, matching every
other `docs/use/` page's convention) covering: the skill catalog
(four P0 skills, one line each, linking to their generated `.agents/skills/
<name>/SKILL.md`), installation (what `.agents/skills/` means, which agents
read it natively per ADR-058's ecosystem research, no per-vendor
re-explanation needed since that's a fact owned by this one page), CLI/tool
prerequisites (same prerequisites `docs/use/cli-usage.md` already states —
link, don't repeat), the security/trust model (link to ADR-058's Safety
invariants, don't restate), and a contribution-guide pointer (back to this
plan + `skills-src/CLAUDE.md`). Explicitly does **not** re-explain ABI vs.
API vs. runtime compatibility, evidence depth, or any other Layer-B concept
already owned by `docs/learn/`/`docs/use/` pages — links only, per
`docs/AGENTS.md`'s "one fact, one place" rule extended to this new page the
same way it already binds every other doc page.

Dogfooding: before any external publication (P1.4+), exercise each of the
four skills against this repository's own recent PRs/examples as a first
correctness pass, recorded as a short "dogfooding notes" addendum to this
plan item's `Status` once run (not a separate doc page).

**Files:** `docs/use/agent-skills.md` (new); `mkdocs.yml` (nav entry under
the existing `Use abicheck` section — placement TBD by whichever docs
person lands this, following the existing nav structure's grouping logic);
`README.md` (one-line pointer alongside the existing "Agent- and
script-friendly" paragraph, not a rewrite of that paragraph).

**Tests:** `mkdocs build --strict` (nav coverage), `scripts/
check_docs_contract.py` (front-matter schema), `scripts/
check_ai_readiness.py`'s `mkdocs-nav-coverage` check.

**Dependencies:** P0.1–P0.3 (the catalog must describe real, generated
skills, not aspirational ones).

**PR boundary:** own PR, lands after P0.2/P0.3 are merged so the catalog
describes what actually exists.

---

## P1 — Reliability and distribution

### P1.1 — Behavioral/e2e evaluation against the examples corpus — **not started**

**Problem:** Structural and trigger tests confirm a skill is well-formed
and discoverable; they don't confirm it reaches the right *answer*.

**Change:** Select a representative subset of the 195 `examples/` cases
covering ADR-058's named behavioral categories (removed export, changed
function signature, struct layout drift, enum value change, vtable change,
API-only break, different compile profiles, public/private scope false
positive, incomplete evidence, non-comparable snapshots, consumer
unaffected despite global break, consumer actually affected, plugin
required-symbol loss, missing matrix target, profile-specific finding);
for each, drive the relevant P0 skill end-to-end against the case's
old/new fixture, and grade against ADR-058's five-point rubric (correct
workflow choice, preserved uncertainty, deterministic evidence obtained
where appropriate, root-cause explanation, appropriate remediation
proposed, no compatibility claim without sufficient evidence) rather than
only "did it invoke abicheck."

**Files:** `validation/scripts/run_skill_evals.py` (new, alongside the
existing `validation/scripts/run_example_owner_proofs.py`-style harness
scripts — reuses `validation/data/manifest.json`'s case indexing rather
than building a parallel index); `validation/data/skill_eval_results.json`
(new results artifact, mirroring the existing `results.json` convention).

**Tests:** the eval harness itself is the test; gate a minimum pass rate in
CI once the first baseline run establishes one (mirroring `SURVIVOR_BASELINE`'s
pattern for mutation testing — establish, then gate on non-regression, not
an arbitrary target chosen up front).

**Dependencies:** P0.1–P0.3, P0.8.

**PR boundary:** own PR per skill is reasonable given the evaluation volume
(four PRs), or one combined PR if reviewed together — team's call.

---

### P1.2 — Finding/root-cause query support (contingent) — **not started**

**Problem:** `native-binary-compatibility-review`'s "group low-level
findings into root causes" step and `native-consumer-compatibility`'s
explanation step both currently do this grouping in skill-side prose logic
over the existing JSON report. Whether that needs a *product* change (a new
query surface) or is fully servable by report post-processing the skill
already does itself is genuinely unknown until P0/P1.1 exercises it for
real.

**Change:** **Do not build this speculatively.** After P1.1's evaluation
pass, if — and only if — the skills' own grouping logic proves inadequate
(loses information the underlying report has, or requires re-deriving
something abicheck's `root_cause_grouping`-equivalent internals already
computed once), scope a minimal addition here. Until then this item stays
a placeholder recording the *question*, not a committed feature.

**Dependencies:** P1.1's findings.

**PR boundary:** N/A until scoped.

---

### P1.3 — Project discovery / baseline-candidate discovery helpers (contingent) — **not started**

**Problem:** Same shape as P1.2 — `native-release-compatibility`'s
"previous supported release selection" and any future "propose a
`.abicheck.yml`" workflow step are currently skill-side reasoning over
existing `project validate`/git-log output. ADR-058 explicitly declines to
resurrect `doctor`/`init`/the baseline registry; this item is where a
narrower, justified need (if any) would be scoped instead.

**Change:** Same discipline as P1.2 — do not build ahead of demonstrated
need. Record here, scope after P1.1.

**Dependencies:** P1.1's findings.

**PR boundary:** N/A until scoped.

---

### P1.4 — Public publication channels — **not started**

**Problem:** ADR-058's publication stages 2–4 (portable `.agents/skills/`
already done by P0; `skills.sh`/GitHub discovery; Claude/Codex/Gemini/Cursor
validation) are distribution steps, not architecture — they follow once
P0/P1.1 establish the skills are actually correct.

**Change:** Submit the four P0 skills to `skills.sh`'s directory once P1.1's
baseline pass rate is acceptable; verify GitHub's own skill-discovery
surfaces (Copilot reads `.agents/skills` directly, per ADR-058's ecosystem
research, so no separate submission step should be needed there beyond the
repo being public). Record actual submission steps taken and any
vendor-specific packaging quirks encountered (e.g. a `skills.sh` "skill
pack" bundling all four together) in this item's `Status` once done —
speculative packaging steps are not pre-specified here since the real
submission UX may differ from what's documented today.

**Dependencies:** P1.1.

**PR boundary:** N/A — this is largely an external-service action, not a
code PR; any repo changes it does require (a `skills.sh` manifest file, if
one turns out to be needed) land as their own small PR.

---

### P1.5 — Cross-agent validation log — **not started**

**Problem:** ADR-058 commits to validating on Claude Code, Codex, Copilot,
and Gemini CLI at minimum, Cursor if current.

**Change:** For each target agent, install the generated `.agents/skills/`
tree (or the agent's own scanned location if different) in a scratch
checkout and run at least one full scenario per P0 skill to completion;
record pass/fail and any agent-specific friction (a skill triggering
incorrectly, a reference file not resolving, a workflow step assuming shell
access the agent doesn't have) in a validation log. Any agent-specific
friction found becomes a P0.2/P0.3 bug fix, not a forked skill copy — per
ADR-058's "no second, independently-authored copy per vendor" rejection.

**Files:** `skills-src/CLAUDE.md`'s validation-log section, or a small
`docs/contribute/agent-skills-validation-log.md` working document modeled
on `docs/contribute/abicc-parity-status.md`'s structure — if the latter,
it must be added to `mkdocs.yml`'s nav under "Parity & Reports" the same
way `abicc-parity-status.md` itself is (every `docs/**/*.md` page must be
nav-reachable or linked from another doc; "working document" is not a nav
exemption, confirmed against how its own model page is actually navigated).

**Tests:** N/A — manual validation, recorded as data, not a CI gate (the
scriptable subset already lives in P0.8).

**Dependencies:** P0.1–P0.3.

**PR boundary:** own PR per validation-log update, or batched — low risk.

---

### P1.6 — CI integration flow — **not started**

**Problem:** ADR-058's admission-criteria decision on CI setup was: not a
fifth public skill, but a documented follow-on action inside
`native-binary-compatibility-review`/`native-release-compatibility`. That
follow-on needs to actually point at real, current instructions.

**Change:** Add a short "wire this into CI" workflow step to both skills'
`SKILL.md` (or a shared `shared/ci-wiring.md` fragment both cite, if the
instructions are identical enough — check during authoring) that points at
the existing `docs/use/github-action.md`/ADR-047 lifecycle rather than
re-explaining Action inputs. No new product surface.

**Files:** whichever of P0.2's two `SKILL.md` files (or a new
`skills-src/shared/ci-wiring.md`) this lands in.

**Dependencies:** P0.2.

**PR boundary:** small, can ride with a P0.2 skill PR or land as its own
tiny follow-up.

---

## P2 — Portfolio expansion (contingent on real usage)

Not committed work — recorded so the admission-bar discipline has somewhere
concrete to point candidates at. **Each item below requires re-applying
ADR-058's five admission criteria with real usage evidence (P1.1's eval
results, actual user requests observed post-publication) before a PR is
opened for it** — this section is explicitly not a backlog to work through
mechanically.

- **Native runtime / OS / container upgrade compatibility** — plausible
  distinct user job ("will this binary work after moving to a new
  container base image"); needs its own evaluation against real
  container-migration scenarios before admission.
- **Compatibility debugging / false-positive investigation** — "why did
  this suddenly report dozens of breaks" is already one of P0's named
  trigger phrasings (routed to `native-binary-compatibility-review`'s
  root-cause step); only worth splitting out if that skill's root-cause
  step proves too shallow for a genuinely distinct false-positive-triage
  decision tree (contingent on P1.1's findings, not decided here).
- **Public ABI/API stability audit** — closest existing overlap is `scan`
  (single-binary audit/lint, G11) and `native-binary-compatibility-review`'s
  "understand intended compatibility contract" step; needs a concretely
  distinct user-visible outcome identified before admission, not just "a
  standalone audit mode."
- **Compiler/client ABI compatibility across profiles** — overlaps
  `compiler-and-build-profiles.md` (Layer B) and G34's producer/consumer
  profile-separation work; evaluate whether this is a distinct public job
  or purely a `native-release-compatibility` matrix concern.
- **Cross-platform compatibility** — likely a workflow branch inside
  existing skills (ELF/PE/Mach-O are already one report shape), not a
  distinct decision tree, pending re-evaluation.
- **Python native-extension compatibility** — real, distinct domain
  (`docs/use/python-extensions.md`'s `abi3` contract already exists as
  product surface); plausible P1/P2 candidate with its own admission pass.
- **Package/binary compatibility** (distro packaging, `.deb`/`.rpm`-level
  concerns) — overlaps `debian_symbols.py`'s existing Debian-symbols
  adapter; evaluate distinctness from `native-release-compatibility`
  before admission.

---

## Answers to ADR-058's explicit questions (cross-reference, not restated)

Questions 1–15 from the task are answered inline in ADR-058's Decision
section (taxonomy, naming, layer model, CLI/MCP, source-of-truth,
versioning) and in this plan's P0/P1 items (product-surface gaps in P0.4/
P0.5, publication minimality in P1.4, evaluation targets in P1.1, the
"generic prompt + abidiff/ABICC" comparison is exactly what P1.1's rubric
— correct workflow choice, preserved uncertainty, deterministic evidence,
root cause, appropriate remediation, no over-claiming — is designed to
measure against, since a generic prompt has no access to abicheck's 396
detected `ChangeKind`s or its contract-coverage/evidence-tier machinery and
cannot preserve uncertainty the way invariant 1–5 require by construction).
