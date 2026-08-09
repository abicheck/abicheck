# ADR-058: Native Compatibility Agent Skills — User-Task-First Domain Layer

**Date:** 2026-08-09
**Status:** Accepted — not implemented. See [G36](../plans/g36-native-compatibility-agent-skills.md)
for the phased implementation plan.
**Decision maker:** (pending — recorded per repository convention)

---

## Context

Every existing abicheck integration surface — the CLI (ADR-037, ADR-043,
ADR-054), the typed Python API and MCP server (ADR-055), and the GitHub
Actions layer (ADR-047) — is reachable only by a caller who already knows
`abicheck` exists and already knows, in outline, which of its verbs answers
their question. ADR-047's own audit of the GitHub Actions surface found the
same failure mode already latent there: `abicheck aggregate` had drifted
into an implicit architectural center because "each addition was locally
reasonable; the result read command-first rather than scenario-first" — a
user had to already know which internal CLI mode
mapped to their scenario before they could pick an Action input. ADR-047's
fix was to reorganize the integration surface around a **project
integration lifecycle** (config → build → evidence → target/baseline
resolution → check → report → fan-in → baseline publish) instead of around
the command set, and ADR-054 later applied the identical discipline to the
CLI root surface itself (a six-part admission bar: "does this answer a
stable, user-facing question" before "does this need a command").

Coding agents (Claude Code, Copilot, Codex, Cursor, Gemini CLI, and others)
have converged in 2026 on a portable, cross-vendor packaging format for
exactly this class of problem — reusable, triggerable domain expertise a
user did not have to already know the name of. The open **Agent Skills**
format (a `SKILL.md` file with YAML frontmatter plus optional
`scripts/`/`references/`/`assets/` subdirectories, originally published by
Anthropic and now maintained as a multi-vendor open standard at
`agentskills.io`) is read natively by Claude Code, GitHub Copilot, OpenAI
Codex, Cursor, and Gemini CLI, each of which independently scans a
`.agents/skills/` directory (in addition to their own vendor-prefixed
locations) as the shared, tool-agnostic convention. A directory
(`skills.sh`, built by Vercel) and one-command installer already exist for
distributing these packages across 18+ agent targets.

A targeted search of that ecosystem (skill marketplaces, GitHub skill
repositories, and general web search — see the "Ecosystem validation"
section below) found **no existing published Agent Skill, in any vendor's
format, that performs deterministic native C/C++ ABI or binary-compatibility
analysis.** What exists in adjacent territory is either generic reference
material never packaged as a skill (Red Hat's libabigail how-to, DPDK's ABI
versioning docs, GCC's libstdc++ ABI policy) or unrelated skills that share
only the word "versioning" (a REST/HTTP API-versioning skill, a skill that
versions *other skills*). The space this ADR addresses — an agent asked
"will this break ABI compatibility for existing consumers," "can I ship this
as a minor version," or "will this old binary still run against the new
library" — is real, recurring, and, as far as this research can determine,
currently unoccupied by any deterministic tool-backed skill.

That is the opportunity. It is not, on its own, a reason to build four more
things that say "abicheck" on the label. ADR-047's central lesson — organize
around the user's scenario, not around the tool's command set — applies with
even more force here, because a Skill's only interface to the user is its
own `name`/`description` (matched against the user's *actual request text*
to decide whether it triggers at all). A skill named after an abicheck verb
is invisible to a user who has never heard of abicheck and is looking for
help with their real problem.

## Problem

Three problems, not one, need solving together:

1. **What should the public skill portfolio actually contain**, at what
   granularity, under what names, so that a user who has never heard of
   abicheck can find and successfully use the right one for a real
   compatibility question?
2. **What is the correct architecture underneath that portfolio** — how
   much is genuinely public/discoverable surface versus shared domain
   knowledge versus an execution backend, and how is that knowledge kept in
   one place (AGENTS.md "M1-1": don't hand-duplicate a fact into multiple
   copies) across five-plus target agent ecosystems without drift?
3. **What, if anything, does abicheck itself need to change** to be a
   trustworthy deterministic backend for these skills — and, symmetrically,
   what must the skills *not* do (fabricate a green result, mutate a
   project silently, treat suppressions as a lever for making output
   quieter) so that "abicheck-verified" keeps meaning something.

## Product positioning

A good skill in this portfolio should answer requests such as these seven —
none of which name abicheck, and each of which a real user could type
without knowing the tool exists:

1. "Review this PR for binary compatibility."
2. "Can I make this public C++ API change without breaking old consumers?"
3. "Can we release this as a minor version?"
4. "Will this old application work with the new library?"
5. "Why did this compatibility check suddenly report dozens of breaks?"
6. "Will this binary still work after moving to a new OS/container?"
7. "How do I keep ABI compatibility across compiler/client profiles?"

abicheck should appear inside the resulting workflow as a deterministic
verification engine, not as the user-facing job. Phrasings 1–4 map directly
onto the four P0 skills (Decision → Public skill taxonomy, below); 5 is
folded into `native-binary-compatibility-review`'s root-cause step; 6 and 7
are P1 candidates (Decision → Skill admission criteria) — real jobs, not yet
admitted for lack of validated usage evidence. This list is the source both
`SKILL.md` `description` fields (Decision → Skill content model) and the
trigger-test positive corpus (Testing and evaluation architecture, and
G36's P0.8) are built from.

## Ecosystem validation (informs Decision, not repeated there)

Researched directly against current (2026) vendor documentation rather than
assumed:

- **Portable format.** `SKILL.md` (YAML frontmatter: `name`, `description`,
  optional `allowed-tools`/`compatibility`/`license`/`metadata`) plus
  `scripts/`, `references/`, `assets/` subdirectories. Three-tier
  progressive disclosure: name+description always loaded (~100 tokens),
  full body loaded only once triggered, bundled files loaded only when
  actually read/executed — the mechanism that lets a skill bundle a large
  reference catalog (e.g. abicheck's full `ChangeKind` taxonomy —
  `checker_policy.py` is its fact owner, not this ADR) at zero standing
  context cost.
- **Canonical portable default, not a claim that vendor-specific
  directories become unnecessary.** `.agents/skills/<skill-name>/SKILL.md`
  is read directly, with no per-vendor generated copy needed, by GitHub
  Copilot (cloud agent, Copilot CLI, Copilot code review, VS Code agent
  mode — alongside its own primary `.github/skills` location, which this
  decision does not deprecate but also does not generate a copy into, since
  Copilot already reads `.agents/skills`), OpenAI Codex (which walks
  `.agents/skills` at every directory level from cwd to repo root), Cursor
  (same — no generated `.cursor/skills` copy either, for the same reason),
  and Gemini CLI. Claude Code is the sole documented exception: it does not
  scan `.agents/skills` at all, so it is the one client the generation
  process (below) produces a real second output tree for —
  `.claude/skills/<skill-name>/SKILL.md`. `.agents/skills` is still this
  document's answer to Task 8's "is `.agents/skills` the best canonical
  publication target" question — it is the right *default* publication
  target precisely because it needs no per-vendor configuration on the
  clients that do honor it, not a claim that every vendor reads it. Source-
  of-truth model, below, defines exactly which output trees are generated
  today (`.agents/skills/`, `.claude/skills/`) versus which vendor-specific
  paths remain a documented but not-yet-implemented option (`.github/
  skills`, `.cursor/skills`) should Testing and evaluation architecture's
  cross-agent validation step ever find one of those clients doesn't
  actually read `.agents/skills` reliably in practice.
- **Self-containment is required by every vendor's own guidance**, not just
  a stylistic preference: a skill is read from the perspective of *one*
  installed directory, and nothing in the format lets an installed skill
  resolve a path outside its own tree. A shared-reference model that leaves
  one skill's `references/` pointing at another skill's directory is not
  merely inelegant — it does not work once the two are installed
  separately (a marketplace zip, a different personal `~/.claude/skills/`
  scope, a `skills.sh` "skill pack" subset install).
- **Plugin/marketplace packaging** (Claude Code's `.claude-plugin/plugin.json`
  + `marketplace.json`) is a superset container — skills, agents, hooks,
  and MCP servers bundled for one-command install — and is optional,
  additive distribution on top of the same `SKILL.md` files, not a
  competing format.
- **MCP Registry** (`registry.modelcontextprotocol.io`) catalogs MCP
  *servers* — a protocol-level tool-connection surface — and is
  independent of Skills distribution. A project can publish skills, an MCP
  server, both, or neither; they do not gate each other.
- **Security model is explicit and unenforced-by-default.** Anthropic's own
  guidance: "use Skills only from trusted sources," treat a skill as
  executable content (a malicious skill can direct tool/code use that does
  not match its stated purpose), and audit every bundled file before
  installing "like installing software." No vendor enforces signing or
  mandatory review; the model is caller due diligence. This directly
  informs the Safety invariants section below — abicheck-authored skills
  must hold themselves to a higher bar than the format requires, precisely
  because nothing external enforces one.

## Design principles

1. **User-task-first, not command-first.** A skill's name and description
   are matched against what a user actually typed. "Review this PR for
   binary compatibility" must trigger a skill; "run abicheck compare" must
   not be required vocabulary. This generalizes ADR-047's finding
   ("scenario-first, not aggregate-centric") from the CI-integration surface
   to the agent-skill surface — same failure mode, same fix, next layer up.
2. **abicheck is a backend, not the brand.** The skill's domain knowledge,
   workflow, and user-visible result must be about the compatibility
   problem. abicheck is invoked because deterministic evidence improves the
   answer, the way a human expert would reach for `nm`/`objdump`/`abidiff`
   — not because the skill exists to demonstrate the tool.
3. **Don't reproduce CLI-surface sprawl one layer up.** ADR-043 collapsed
   the CLI root surface from ten commands down to five precisely because a
   large surface of narrowly-scoped, similarly-named verbs is worse for a
   caller than a smaller number of well-chosen ones (it later grew back to
   seven — `aggregate` then `project` — each addition individually clearing
   ADR-054's admission bar, not a relapse into the original sprawl). A
   public skill portfolio is subject
   to the identical failure mode and the identical fix: an explicit
   admission bar (below), applied to every candidate, not "one skill per
   scenario we can think of."
4. **CLI is the normative execution backend; MCP is an optional adapter.**
   Skill correctness must never depend on MCP being configured. See
   Decision §"Execution backend" below.
5. **A skill must never manufacture a false green result.** This is
   non-negotiable and is elaborated fully in Safety invariants below — it
   is listed here as a design principle because it constrains every other
   decision in this document (e.g. it is why `not_comparable` cannot be
   collapsed to "pass" anywhere in a skill's decision tree).
6. **One fact, one place.** Domain knowledge that is genuinely shared across
   skills (what a comparability failure means, how evidence depth ladders
   work) is written once and referenced, not re-explained per skill —
   `docs/AGENTS.md`'s "one fact is defined in exactly one place" rule,
   applied to the skill-source tree the same way it already applies to
   `docs/`.

## Decision

### Public skill taxonomy

Publish **four initial (P0) public skills**, evaluated against the
admission criteria below and found each to be a distinct, standalone-useful
unit of work — none merges cleanly into another without losing a
user-visible, distinct decision tree:

| Skill | User job | Distinct because |
|---|---|---|
| `native-binary-compatibility-review` | "Will this change break existing consumers?" — review a diff/branch/commit/PR | The only skill whose job is *diagnose an already-made change*; ends in a verdict + root-cause explanation, not a design or release decision |
| `native-api-evolution` | "How do I make this API change without breaking compatibility?" — design-time guidance | The only skill that runs *before* a change exists; its expertise (pImpl, versioned interfaces, reserved slots, deprecation lifecycle) is proactive design knowledge, not diff analysis. Verification of the resulting change is a call *into* `native-binary-compatibility-review`'s machinery, not a duplicate of it |
| `native-release-compatibility` | "Can we ship this as 1.x, or does it need a major bump?" — a release/versioning decision | The only skill whose object is a *release* (SONAME, semver, multi-library/multi-platform gate), not a single diff; a release can be compatible per-diff yet still blocked by an incomplete evidence matrix, which no per-change review answers |
| `native-consumer-compatibility` | "Will *this specific* application/plugin/host keep working?" — a scoped, consumer-relative question | The only skill whose answer can diverge from the library's own global verdict (globally breaking, but this consumer is unaffected, or vice versa) — a genuinely different decision tree, not a filtered view of the review skill's output |

**Rejected naming pattern:** `abicheck-review-pr`, `abicheck-release-gate`,
`abicheck-evidence-doctor` and any other `abicheck-<verb>`-shaped name.
These describe *how to operate a tool*, and — per the design principles —
a skill discovered by its own product's name has already failed the
"user doesn't need to know abicheck exists" bar before its description is
even read.

**`native-*` prefix, validated rather than assumed (Question 2).** The
prefix must (a) not collide with a REST/HTTP/generic-software-API skill
namespace, which would misfire the trigger on "review this API change" for
a web-service caller, and (b) not read as a product brand. `native-`
qualifies both: it is a real, load-bearing word in this domain (it is how
this ADR's own source material — DPDK, GCC, Android's VNDK docs —
distinguishes compiled-library ABI concerns from managed-runtime/REST ones),
and the ecosystem search in this ADR found no colliding `native-*` skill.
It is not an abicheck brand token, so it survives a future rename of the
underlying tool.

### Skill admission criteria

Applied to every *candidate* skill, present portfolio and future proposals
alike (Question 1, and the P1/P2 gating in the Implementation Plan):

1. **Distinct user intent** — a real person would type this request without
   already knowing abicheck's vocabulary.
2. **Distinct decision tree** — its workflow branches differently from
   every other public skill's, not just its input arguments.
3. **Distinct user-visible outcome** — the thing the user walks away with
   (a verdict, a design recommendation, a release decision, a
   yes/no-for-this-consumer) differs from every other public skill's.
4. **Useful standalone discovery query** — someone could plausibly install
   *only* this skill and get value, without the rest of the portfolio.
5. **Enough specialized domain knowledge to justify a skill**, not a
   reference page or an internal branch inside another skill's workflow.

A candidate that fails any of these becomes **shared domain knowledge**
(Layer B below) or an **internal workflow branch** inside an existing
public skill — never a fifth+ public skill by default. This directly
answers Questions 3 and 4:

- **Application vs. plugin/host consumer compatibility (Question 3)**: one
  public skill, `native-consumer-compatibility`, with two internal
  branches (an application importing the library directly vs. a
  plugin/host with required-entrypoint semantics). Both branches ask "will
  consumer X keep working," differ only in *how* the consumer's required
  surface is established (imported-symbol scan vs. plugin ABI contract) —
  criterion 2 (distinct decision tree) is not met at the public-skill
  level, only at the CLI-flag level (`--used-by` vs.
  `--required-symbol(s)`), which is exactly the level ADR-043 D2 already
  folded these into one CLI verb (`compare`) for the identical reason.
- **CI setup as a public skill (Question 4)**: not a fifth public skill. It
  fails criterion 3 — "set up CI" has no user-visible *compatibility*
  outcome of its own; it is a mechanical follow-on once a review or release
  decision already exists. It ships as a documented action a skill performs
  at the end of `native-binary-compatibility-review` or
  `native-release-compatibility` ("wire this gate into your PR checks"),
  pointing at the existing GitHub Action / ADR-047 lifecycle rather than
  re-explaining it.

**P1 candidates, evaluated and deliberately deferred, not rejected**
(runtime/OS/container upgrade compatibility, compatibility-debugging /
false-positive investigation, public ABI/API stability audit,
compiler/client ABI compatibility, cross-platform compatibility, Python
native-extension compatibility, package/binary compatibility): several of
these plausibly clear the admission bar on inspection (runtime/container
upgrade and Python-extension compatibility look like real, distinct user
jobs), but none has a validated real-usage case yet, and admitting them
speculatively repeats the exact mistake ADR-047 diagnosed. They are
recorded as P1/P2 candidates in the companion plan, each to be re-evaluated
against the same five criteria with real usage evidence before
publication — not bundled into P0.

### Layer model

Three conceptual layers, mirroring ADR-037's Tier-1/2/3 split one
abstraction level up:

**Layer A — Public user-task skills.** The four (eventually more, subject
to the admission bar) skills in `.agents/skills/`. Discoverable by user
intent. This is the *only* layer with a `SKILL.md` `name`/`description`
that competes for triggering — everything below is invisible to skill
discovery.

**Layer B — Shared native-compatibility domain knowledge.** Reusable
concepts every Layer-A skill draws on, written once: compatibility
contracts (ABI vs. source-API vs. runtime compatibility — three genuinely
different questions the domain conflates at its peril), baseline selection,
extraction/comparability contracts (what makes an old/new pair even
answerable — ADR-050), evidence depth selection (L0–L5, `docs/learn/
evidence-and-detectability.md`'s existing "what each layer buys" material),
public/private surface scoping, compiler/build profiles, consumer scoping
(ADR-057's consumer graph), policies/suppressions, report interpretation,
root-cause grouping, remediation patterns, and uncertainty/coverage
semantics (contract-coverage exit, ADR-049 Phase 7). These are **not**
separate public skills — they have no standalone user-facing trigger — but
they must exist as exactly one canonical, reference-linkable copy each
(Question 5), consumed by every Layer-A skill that needs them, so that (for
example) a change to how comparability failures are explained updates all
four skills' behavior from one file. Layer B content lives under
`skills-src/shared/` (source layout below) and is compiled into each
skill's own `references/` at publish time — not left as a live cross-skill
symlink, per the self-containment requirement the ecosystem research
confirmed every vendor needs.

**Layer C — Execution backends.** abicheck CLI (normative), Git, the
project's own compiler/build system, `nm`/`readelf`/`objdump`/`dumpbin`
where they help, the Python API, GitHub Actions, and MCP as an optional
adapter. A Layer-A skill's workflow may *use* Layer C, but its
`SKILL.md` must not read as documentation for Layer C — the moment a
skill's decision tree exists to explain a CLI flag rather than to solve
the user's problem, it has drifted out of Layer A and belongs in Layer B's
reference material (linked, not inlined) or in abicheck's own `docs/use/`.

### Execution backend: CLI is normative, MCP is optional

Local CLI invocation is the default and required execution path for every
P0 skill (Question 8: yes, current CLI fully supports all four P0
workflows without MCP — verified against the CLI grounding: `compare`
already carries `--used-by`/`--required-symbol(s)` consumer scoping,
`--policy`/`--suppression-file`/severity flags for release-gate decisions,
`--contract`/`--contract-evaluation` for contract-relevance domains, and
`project`/`aggregate` for multi-target/multi-profile releases — nothing a
P0 skill needs is MCP-only). Reasons, validated rather than assumed:

- Essentially every coding agent this document targets (Claude Code,
  Copilot CLI/cloud agent, Codex, Cursor, Gemini CLI) has shell access;
  none of them is guaranteed to have MCP configured.
- CLI commands a skill runs are reproducible by a human reading the
  transcript, and identical to what CI runs — one semantic model, not two.
- MCP configuration is a separate setup step outside the skill's own
  installation; requiring it would make the skill's *installability*
  conditional on something outside the skill format.
- Keeping MCP optional keeps the skill portable to an environment (a CI
  runner, a minimal agent sandbox) that never configures MCP at all.

MCP remains a legitimate **adapter** for a tool-mediated client without
shell access, and nothing in this decision deprecates it — ADR-055 already
made the CLI, typed API, and MCP resolve through one shared chokepoint, so
a skill that *does* have MCP available may prefer it for structured-output
convenience without behaving differently. What changes is only that no
skill's correctness, installability, or admission depends on MCP being
present (Question 9: nothing in the P0 portfolio is MCP-only or
Python-API-only; a future skill designed for a tool-mediated,
no-shell client — not part of this ADR's P0 scope — is the only case where
that could legitimately flip).

### Source-of-truth and publication model

```text
skills-src/                              # editable source (this repo, DRY)
  shared/                                # Layer B: domain knowledge fragments
    compatibility-contracts.md
    evidence-and-depth.md
    baseline-and-comparability.md
    public-surface-and-scoping.md
    compiler-and-build-profiles.md
    consumer-scoping.md
    policies-and-suppressions.md
    report-interpretation.md
    root-cause-grouping.md
    remediation-catalog.md
    safety-invariants.md
  native-binary-compatibility-review/
    SKILL.md
    references/                          # skill-specific reference material only
  native-api-evolution/
    SKILL.md
    references/
  native-release-compatibility/
    SKILL.md
    references/
  native-consumer-compatibility/
    SKILL.md
    references/

scripts/gen_agent_skills.py              # the sole generator (Layer C
                                          # tooling, not part of skills-src/
                                          # itself) — builds .agents/skills/
                                          # and .claude/skills/ from the above

.agents/skills/                          # GENERATED, canonical publication surface
  native-binary-compatibility-review/
    SKILL.md                             # skills-src copy + compiled-in shared/ refs it uses
    references/
      ...own references, plus the shared/ fragments this skill actually cites...
  native-api-evolution/
    SKILL.md
    references/
  native-release-compatibility/
    SKILL.md
    references/
  native-consumer-compatibility/
    SKILL.md
    references/
```

- `.agents/skills/` is the **authoritative publication target** — see
  "Canonical portable default" above for which clients read it directly and
  why Claude Code is the one documented exception. It is generated, not
  hand-edited; a generator script (Layer C tooling,
  `scripts/gen_agent_skills.py` in the implementation plan) resolves each
  skill's `references:` manifest (which `shared/` fragments it actually
  uses) and copies/renders the result into this tree — **no symlinks**, per
  the ecosystem research finding that a marketplace zip, a `skills.sh`
  skill-pack subset install, or a Windows checkout of a symlinked tree each
  break a live cross-skill link differently. Generation keeps every
  installed skill genuinely self-contained (the requirement every vendor's
  own docs impose) while keeping `skills-src/shared/` as the one editable
  copy of any fact more than one skill needs (design principle 6).
- **`.claude/skills/` is the one additional generated packaging target
  today** — a thin **copy** (never a symlink, same invariant as above) of
  the resolved `.agents/skills/<name>/` content, rendered by the same
  generator into this second tree, because Claude Code needs its own
  committed copy (see above), not because it is "dogfooded alongside" the
  portable target as an optional convenience. `.github/skills/` and
  `.cursor/skills/` are **not** generated: Copilot and Cursor both already
  read `.agents/skills/` directly, so a generated copy into their own
  vendor-specific paths would be redundant output with nothing reading it.
  A future Claude Code plugin bundle remains a possible additional
  packaging target but is not part of this decision's committed scope.
  None of these hand-maintains its own prose.
- **`skills.sh` is a separate, external distribution channel, not a
  generated filesystem tree** — a `skills.sh` skill-pack listing is P1.4's
  concern (a submission with its own manifest, potentially bundling a
  *subset* of the four skills rather than a 1:1 copy of a generated
  directory), not a third output of `scripts/gen_agent_skills.py` alongside
  `.agents/skills/`/`.claude/skills/`. The self-containment invariant above
  still applies to whatever a `skills.sh` submission bundles — an installed
  subset must never depend on a path outside its own package — but that
  submission's shape is a distribution-time decision, not part of this
  generator's own committed output trees.
- **abicheck/abicheck stays the single authoritative repository.** Nothing
  in this model requires a second repository; `skills-src/` and
  `.agents/skills/` are ordinary tracked directories in this repo, gated by
  the same CI (drift tests below) as every other generated-doc pattern this
  repository already has (`docs/reference/cli-reference.md`,
  `docs/reference/mcp-tools-reference.md`, etc. — `docs/AGENTS.md`'s
  "regenerating generated docs" contract, applied to a new generated
  artifact family the same way).

### Skill content model

`SKILL.md` (Layer A, per skill) contains only: triggering/user-intent
framing (the `description` field IS the discovery mechanism — it must
name the real-world questions from this ADR's Product positioning
verbatim-ish, not abicheck vocabulary), the workflow/decision tree, safety
and uncertainty rules (linking Layer B's `safety-invariants.md`, not
re-stating it), tool-selection principles (when to reach for the CLI vs.
when the answer is already knowable), the expected outcome shape, and
termination/verification criteria (when is the job actually done, e.g. "re-run
after remediation"). Anything long, technical, or reference-shaped —
ABI design pattern catalogs, the full CLI recipe list, comparability-failure
reason codes, the remediation catalog, report-schema field meanings — lives
under `references/`, most of it in the shared Layer-B fragments referenced
above rather than duplicated per skill. **Do not hand-copy** the CLI
reference, the MCP tools reference, or the `ChangeKind` catalog
into a skill's `references/` — link/generate from the canonical
`docs/reference/` sources the same generator step already touches, so a
CLI flag rename or a new `ChangeKind` cannot silently leave a skill
describing removed surface (this is what the drift-testing section commits
to, below).

### Required abicheck product capabilities

Audited against what exists today before proposing anything new —
consistent with "audit before adding," `AGENTS.md`'s "don't add
dependencies/surface without strong justification":

**Already sufficient, no change needed:**
- `compare --format json` / `scan --format json` already emit a
  machine-readable verdict, per-finding kind/severity/location, evidence
  tier, and (under `--contract-evaluation`) contract coverage and
  compatibility-decision blocks (ADR-049 Phases 4–7) — this already
  satisfies the compact-decision-summary need in substance, for the fields
  ADR-049/055 already added.
- Consumer scoping (`--used-by`, `--required-symbol(s)`), contract-mode
  selection (`--contract public|exports|all`), and multi-target/profile
  release gating (`project`, `aggregate`, `--exit-code-scheme`) are all
  live CLI surface a skill can drive directly.
- Structured comparability failure already exists in substance, on a
  known, existing field: a `not_comparable` result (`ProfileMismatchError`/
  `ScopeMismatchError`, ADR-050 D1/D2) already renders as a top-level
  `"reason": {"kind": ..., "message": ...}` object in the `--format json`
  document (per `REPORT_SCHEMA_VERSION` — `abicheck/schemas/__init__.py`'s
  own fact-owned constant, not restated here as a literal since it moves
  independently of this ADR; `compare_report.schema.json`;
  `cli_compare_helpers._report_not_comparable`) — today `kind` is one of
  exactly two coarse values, `profile_mismatch` or `scope_mismatch`, with
  the specific mismatched field only recoverable from the free-text
  `message`. This is what the gap below promotes, not a `comparability`
  block that doesn't exist in the current schema.

**Real, minimal gaps (P0 — do only these, no speculative surface):**
- **`abicheck info --format json`** does not exist today (confirmed: no
  `info` command in `abicheck/cli*.py`). A skill deciding "is my installed
  abicheck new enough for `--contract`," or "which extraction providers are
  available on this host," has no machine-readable way to ask — it would
  otherwise have to parse `--version`'s human-oriented string or probe by
  trial-and-error. This capability is needed; its exact surface is not yet
  settled. It does **not** cleanly clear `AGENTS.md`'s CLI-command
  admission bar (ADR-054 D6) as a new root command — its operand-free
  shape fails criterion 2 — so G36 P0.4 records this as blocked on an
  explicit, upfront maintainer decision between an approved bar exception
  and a redesign (e.g. extending `--version` with `--format json` instead
  of adding a new verb) before implementation starts, not as a foregone
  `info` command.
- **A finer-grained `reason.codes` array on the existing `not_comparable`
  object.** Rather than inventing a new top-level block, extend the
  existing `reason` object with a `codes` field — an array, since
  `check_contracts_comparable` can raise on multiple simultaneously
  mismatched fields (e.g. `compiler_family` and `abi_dialect` both
  differing at once) and a singular code would force discarding a cause or
  inventing an arbitrary precedence. Values are a documented, closed enum
  covering every `PROFILE_FIELD_KEYS`/`_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`
  (including the DPC++-only `frontend_context_kind`)/`SCOPE_FIELD_KEYS`
  mismatch cause `comparability.py` already checks, each mapped to a stable
  code, with an explicit `other_profile_mismatch`/`other_scope_mismatch`
  fallback for any field not individually enumerated (so a future
  `SCOPE_FIELD_KEYS` addition degrades to a generic-but-still-typed code
  instead of silently emitting nothing) — never collapsed to the two
  coarse existing `kind` values a skill would otherwise have to re-derive
  the real cause from free text. This completeness promise is explicitly
  scoped to *within* whichever single exception the comparability gate
  raises — `check_contracts_comparable`'s checks early-return, so a pair
  differing on both scope and profile fields still surfaces only the first
  domain's codes on one run; closing that would mean restructuring the
  gate's control flow, a separate change out of scope here (see G36 P0.5
  for the exact boundary). MCP's `abi_compare` envelope emits
  `reason` as a bare string today, not an object, so its own counterpart
  is an additive sibling field (`reason_codes`) alongside the unchanged
  string `reason`, not a type migration. This is a report-schema addition
  (a `REPORT_SCHEMA_VERSION`-gated field — the compare-report JSON contract's
  own version constant; distinct from `serialization.SCHEMA_VERSION`, which
  versions persisted `AbiSnapshot` files, not reports), not a new command;
  see G36 P0.5 for the
  exact field-by-field mapping and test matrix.
- Every other candidate capability considered (finding/root-cause querying,
  project discovery, baseline-candidate discovery) is **P1, contingent on
  the P0 skills actually needing it in practice** — not committed here. In
  particular this ADR explicitly does **not** resurrect
  `doctor`, `init`, or the old baseline registry (ADR-043's removals stand);
  a skill that needs "propose a `.abicheck.yml`" can compose that from
  `project validate`'s existing error output plus its own domain reasoning,
  without a new generative CLI command.

### Versioning and drift model

- **Skill version is pinned to the abicheck version whose CLI surface and
  report schema it was written against**, recorded in each `SKILL.md`'s
  `metadata` frontmatter field (not a separate versioning scheme) — pre-1.0
  abicheck (`pyproject.toml`'s `version` key is the fact owner, not a
  literal copied into this document) makes no compatibility promise
  between minor versions (ADR-043's framing), so a skill
  must state the abicheck version range it was validated against and the
  drift tests (below) must fail loudly, not silently degrade, when that
  range is exceeded.
- **Generated reference material can never go stale silently** the way a
  hand-copied CLI cheat-sheet would: because Layer A/B reference content is
  generated from the same canonical sources `docs/reference/cli-reference.md`
  and `docs/reference/mcp-tools-reference.md` already regenerate from
  (`scripts/gen_cli_reference.py`, `scripts/gen_mcp_reference.py`), a
  renamed or removed CLI flag a skill's workflow depends on is caught by
  the identical CI gate (`scripts/verify.py --profile pr`'s doc-drift
  checks) already enforced for every other generated doc — see the
  Implementation Plan's drift-test item for the concrete new CI step.

## Safety invariants

Non-negotiable, and identical across all four P0 skills — encoded once in
`skills-src/shared/safety-invariants.md` (Layer B) and linked, not restated,
from every `SKILL.md`:

1. **Missing evidence is not evidence of compatibility.** A finding
   category abicheck could not check with the evidence given (e.g. no DWARF
   for layout, no build evidence for L3+) must be reported as unverified,
   never silently folded into "no findings = compatible."
2. **`not_comparable` is not a pass.** A skill must never present a
   comparability failure as "no breaking changes found" — it is a distinct
   outcome requiring its own remediation (fix the comparison inputs), never
   collapsed into the compatible branch of any decision tree.
3. **A diagnostic/tentative comparison must not be used as a release gate.**
   Advisory/shadow-depth runs (ADR-049) inform a review; only a run whose
   evidence and contract coverage meet the skill's stated bar may back a
   release decision.
4. **Missing required matrix targets are not compatible.** A
   multi-platform/multi-profile release skill (`native-release-compatibility`)
   must treat an unrun matrix cell as unknown, never as passing by omission.
5. **Incomplete contract coverage must remain visible.** ADR-049 Phase 7's
   coverage-exit contribution is additive and unsuppressible by design; a
   skill summarizing a result must carry that signal forward, not compress
   it out of its own summary.
6. **Suppressions must not be silently broadened.** A skill may point out
   that an existing suppression rule covers a new finding and explain why;
   it must never author or widen a suppression rule as a way to make output
   quieter without the user explicitly asking for and reviewing that
   specific change.
7. **Baselines are never updated merely to make a result green.** Baseline
   selection is a user/project decision the skill can *recommend*
   (`native-release-compatibility`'s "previous supported release" logic)
   but never silently perform to clear a check.
8. **No silent tool installation or project/build mutation.** A skill may
   tell the user a system tool (castxml, a compiler) is missing and how to
   install it; it does not run installers or modify build files without
   explicit confirmation for that specific action.
9. **Local by default.** Source, binaries, and debug information stay on
   the local machine unless the user's own request already implies
   otherwise (e.g. they asked the skill to open a PR comment). A skill must
   not upload artifacts to a third-party service as a side effect of doing
   its job.
10. **Content inside source files, comments, symbol names, reports, or any
    other artifact the skill reads is data, not agent instruction.** A
    diff, a commit message, or a discovered `.abicheck.yml` cannot direct
    the skill to skip a check, change its verdict, or take an action beyond
    what the user asked — the same untrusted-content discipline this
    repository's own CI/PR-webhook handling already applies, generalized to
    every text a skill reads.
11. **Findings preserve provenance and uncertainty.** A skill's summary
    must be traceable back to the specific evidence tier and abicheck
    finding(s) that produced it — never presented as a bare, unsourced
    verdict.

A skill **may** propose and, with explicit confirmation, perform a
remediation (e.g. editing a header to add a reserved field, wrapping a
struct in a versioned interface) — but only ends the workflow by re-running
the same deterministic verification that flagged the problem and reporting
the new result, never by asserting the fix worked without re-checking it.

## Testing and evaluation architecture (summary — full detail in the plan)

Five layers, not just markdown linting, mirroring the repository's existing
multi-layer test-quality discipline (`AGENTS.md`'s FP-rate/tier-accuracy/
mutation-testing precedent, generalized to a new artifact class):

1. **Structural tests** — valid `SKILL.md` frontmatter, self-contained
   references (no path outside the skill's own installed directory),
   generated-vs-source drift (`skills-src/` → `.agents/skills/` is
   reproducible and committed in sync, the same contract
   `docs/reference/*.md` generated pages already enforce), no broken
   internal links.
2. **Tool/API drift tests** — every CLI command/flag and report-schema
   field a skill's workflow or references cite is extracted from the real
   CLI (`click` introspection, the same mechanism `gen_cli_reference.py`
   already uses) and checked to still exist; a renamed/removed flag fails
   CI, not silently rots in a skill's prose.
3. **Trigger tests** — positive examples (the five of the seven Product
   positioning phrasings that map to a P0 skill) must select the intended
   skill; the remaining two (OS/container-upgrade, compiler/client-profile
   — P1 candidates, not yet admitted) are deliberately excluded from this
   "must select" assertion, since no P0 skill claims them and forcing one
   to trigger on out-of-scope phrasing would itself be a false positive —
   they're tracked instead as an explicit "not yet claimed, not
   mishandled" case (G36 P0.8). Negative examples (REST/OpenAPI
   compatibility, database migrations, Java API compatibility, arbitrary
   JSON-schema compatibility) must not false-trigger any native-* skill.
4. **Behavioral/e2e evaluation**, reusing the existing
   `examples/`/`ground_truth.json` corpus and `validation/` harness rather
   than building a parallel one — a skill is graded on workflow choice,
   preserved uncertainty, evidence obtained, root-cause explanation,
   proposed remediation, and never claiming compatibility without
   sufficient evidence (the same rubric this ADR's safety invariants
   define), not merely on "did it call abicheck."
5. **Cross-agent validation** — one canonical skill implementation
   (`skills-src/`), validated to actually trigger and complete correctly
   on Claude Code, Codex, GitHub Copilot, and Gemini CLI at minimum (Cursor
   included if its skill support is current), with agent-specific
   adaptation limited to packaging, never to duplicated prose.

## Consequences

**Positive:**
- Fills a real, validated ecosystem gap rather than adding a speculative
  feature no one asked for.
- Extends this repository's own established discipline (scenario-first
  integration surfaces, one canonical fact per concept, generated docs with
  drift gates) to a new distribution channel instead of inventing new
  conventions for it.
- The admission-bar discipline caps portfolio growth the same way ADR-054
  capped CLI root-command growth — a known, repeatable failure mode this
  repository has already paid down once.
- Near-zero required product surface change (`abicheck info`, one report
  field) — most of what P0 needs already exists.

**Trade-offs / costs:**
- A new generated-artifact family (`skills-src/` → `.agents/skills/`) is
  another thing `scripts/verify.py --profile pr` must gate, and another
  category `scripts/check_ai_readiness.py`-style drift checks must cover —
  real, ongoing maintenance surface, not a one-time cost.
- Skill correctness now depends on staying within abicheck's own pre-1.0
  stability envelope; every CLI-surface or report-schema change this
  repository makes must consider "does a published skill reference this,"
  which is a new discipline this ADR imposes on unrelated future PRs (the
  drift tests are the mitigation, not a substitute for authors' own
  awareness).
- Publishing outside this repository (skills.sh, a Claude Code plugin
  marketplace entry) introduces a distribution surface this repository does
  not fully control the update cadence of — mitigated by treating those as
  thin, regenerable packaging targets (per the source-of-truth model), never
  a second place prose is authored.

## Alternatives rejected

- **Product-centric naming** (`abicheck-review-pr`, `abicheck-release-gate`,
  `abicheck-evidence-doctor`) — rejected per the Design principles and
  Decision sections above; describes operating the tool, not solving the
  user's problem, and is explicitly the anti-pattern this ADR exists to
  avoid.
- **One skill per internal technical branch** (a separate public skill for
  "application consumer" vs. "plugin/host consumer," or one per evidence
  tier) — rejected by the admission criteria (Question 3): these are
  workflow branches inside one distinct user-visible outcome, and
  publishing them separately reproduces the CLI-surface sprawl ADR-043
  already fixed once, one layer up.
- **MCP as the primary/required execution model** — rejected in the
  Decision section: makes skill correctness conditional on configuration
  outside the skill's own installation, and no P0 workflow needs it.
- **A second, independently-authored copy of the skill per target vendor**
  — rejected: violates `AGENTS.md`'s "one fact, one place" and guarantees
  drift the moment any vendor's copy is patched in isolation; the generated
  `skills-src/` → per-target-output model is the fix.
- **Symlink-based publication** from `skills-src/` straight into vendor
  directories — rejected per the ecosystem research: breaks under
  marketplace zip packaging, `skills.sh` skill-pack subset installs, and
  Windows checkouts; a generator that copies/renders is required instead.
- **Resurrecting `doctor`/`init`/the baseline registry** to support skill
  workflows — rejected; ADR-043's removals stand, and nothing in the P0
  skill set actually requires them (see Required abicheck product
  capabilities above).

## Relationship to existing ADRs

- **ADR-047** (GitHub Actions Integration Model) is this ADR's direct
  precedent and is generalized, not superseded: "organize around user
  scenarios, not internal commands/aggregates" is applied here to a new
  surface (agent skills) the same way ADR-047 applied it to CI Actions.
- **ADR-043 / ADR-054** (CLI surface reset and consolidation) supply the
  admission-bar discipline this ADR's skill admission criteria are modeled
  on, and this ADR explicitly does not reopen their scope decisions
  (`doctor`/`init`/baseline registry stay removed).
- **ADR-037** (CLI Interface Contract) supplies the Tier-1/2/3 pattern this
  ADR's Layer A/B/C model mirrors one level up, and is the reason "CLI is
  normative" is a safe default — every P0 workflow already routes through
  the same Tier-2 chokepoint regardless of which front-end a skill drives.
- **ADR-055** (Typed Request/Result Completeness and Schema Registry) is
  why the CLI and MCP already resolve through one shared implementation —
  a skill's optional MCP path cannot silently diverge from its CLI path,
  because ADR-055 D4 already closed that gap at the product level.
- **ADR-049** (Contract Relevance and Compatibility Configuration) and
  **ADR-057** (Consumer Graph) supply, respectively, the contract-mode/
  coverage machinery `native-release-compatibility` needs and the
  `--used-by` root-cause machinery `native-consumer-compatibility` needs —
  both already implemented, confirmed against current code in this ADR's
  grounding pass.
- **ADR-050** (Comparability Contract) supplies the underlying data the
  new stable comparability-reason-code field (Required product
  capabilities, above) promotes to a top-level report field.
- **ADR-051** (Documentation Operational Model) is the precedent for
  "generated content, drift-gated by CI, one canonical source" that this
  ADR's skill-generation model follows for a new artifact type.

## Validation criteria

This ADR is validated when:

1. All four P0 skills exist under `.agents/skills/`, generated from
   `skills-src/`, and each independently passes the structural, drift,
   trigger, and behavioral tests in the Testing section.
2. Each P0 skill has been exercised end-to-end against at least the
   `examples/` cases named in the companion plan's evaluation matrix — the
   concrete case-ID-to-skill mapping this criterion is checked against is
   G36 P1.1's own `validation/scripts/run_skill_evals.py` case selection
   plus `validation/data/skill_eval_scenarios.yaml`'s scenario manifest
   (P1.1's "Files" list), not a matrix defined in this ADR — with the
   skill reaching the documented ground-truth verdict and preserving
   uncertainty where the example is deliberately incomplete-evidence.
3. Whichever machine-readable capability-discovery surface the maintainer
   decision in G36 P0.4 settles on (an `info` command under an approved
   bar exception, or an extended `--version --format json`) and the
   comparability reason-code field both ship, are covered by tests, and
   are consumed by at least one P0 skill's workflow (not added
   speculatively and left unused) — this criterion is about the
   capability, not a specific command spelling that P0.4 itself leaves
   open.
4. The trigger-test negative set (REST/OpenAPI, DB migrations, Java API,
   generic JSON-schema compatibility) does not false-trigger any `native-*`
   skill, confirmed by an automated test, not manual spot-checking.
5. All four of the Testing section's named minimum cross-agent targets —
   Claude Code, Codex, Copilot, and Gemini CLI (Cursor is the one target
   this ADR itself marks conditional, "if current") — have each been used
   to run *every* P0 skill through at least one real scenario end-to-end,
   per G36 P1.5's per-target/per-skill validation-log procedure, with
   results recorded there. Exercising only two targets, or only one skill
   per target, does not satisfy this criterion — G36 P1.4's own publication
   gate already depends on this same full-coverage bar (see G36 P1.4/P1.5),
   and this criterion is stated to match it rather than a narrower one.
