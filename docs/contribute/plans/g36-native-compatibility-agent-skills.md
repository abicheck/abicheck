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

> **Amendment (2026-08-09).** #684, merged the same day this plan was
> written, removed the MCP server (`abicheck-mcp`, `abicheck/mcp_server.py`
> and its sibling modules) entirely — see
> [ADR-021b](../adr/021-mcp-security-model.md) and
> [ADR-055's retirement note](../adr/055-typed-request-result-completeness-and-schema-registry.md).
> None of this plan's items were started against this backend, so nothing
> here needed unwinding. P0.5's `codes`/`reason_codes` item below has been
> updated in place: it no longer requires an `abicheck/mcp_server.py` edit
> or an MCP `abi_compare` envelope test, and its producer-surface count is
> six (`compare`, `scan`, the release JSON, the release summary,
> `aggregate`, `deps compare`/stack), not seven. The two remaining MCP
> mentions left in P0.5 are explicitly historical (why the field was
> originally scoped that way), not implementation targets. No other phase
> in this plan referenced MCP. This plan's actual scope — the four P0
> skills, `skills-src/` → `.agents/skills/`/`.claude/skills/`/
> `.gemini/skills/` generation, the CLI-only execution backend — was never
> MCP-dependent and is unaffected.

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

### P0.1 — `skills-src/` source tree and shared Layer-B fragments — **done**

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
contract-coverage/finding blocks), `root-cause-grouping.md` (the primary
content here is: use `compare --report-mode root-cause --format json`'s
existing deterministic `root_causes`/`root_cause_count` fields first —
don't re-derive a grouping from the flat finding list in skill-side prose,
which risks a lower-fidelity result that diverges from abicheck's own
answer; this fragment documents that mode's shape and only then covers
the residual cases it doesn't already handle),
`remediation-catalog.md` (the pattern catalog ADR-058's
`native-api-evolution` skill draws from: pImpl/opaque handles, compatibility
wrappers, overloads over destructive signature changes, reserved
fields/slots, versioned interfaces, plugin capability negotiation,
deprecation/migration lifecycle), and `safety-invariants.md` — the
**operational** copy of ADR-058's eleven numbered invariants, every
`SKILL.md` links this file rather than restating it. It is a one-time
extraction from the ADR at the point this item is implemented, not a
second document independently maintained in parallel: ADR-058's own text
is a decision record (like every other ADR in this repo, it is not edited
again after acceptance — only its Status line changes), so once this
fragment exists it — not the frozen ADR prose — is where a future safety
correction actually gets made and where every skill picks it up from,
closing the two-manually-maintained-copies risk a living second copy would
otherwise create. Each fragment states, at its top, which
canonical `docs/learn/`/`docs/use/`/`docs/reference/` page(s) it summarizes
— not just informally, but registered as real `summarizes:` entries against
the corresponding topic(s) in `docs/_meta/topics.yaml` (P0.6 is where this
registration actually happens; see that item for the mechanism this
paragraph only motivates).

**Files:** `skills-src/shared/*.md` (11 new files);
`skills-src/CLAUDE.md` (required — this is a new major sub-tree per
`scripts/check_ai_readiness.py`'s `claude-md-coverage` check once it's added
to `REQUIRED_CLAUDE_MD_DIRS`, see P0.6).

**Tests:** none yet (structural tests land in P0.7, not P0.5 — P0.5 is the
comparability-reason-code product change and defines no skill-structural
tests of its own); a manual editorial pass
confirming each fragment is self-contained prose (no unresolved
cross-fragment references) before P0.2 starts consuming them.

**Dependencies:** none — this is the first item.

**PR boundary:** one PR per fragment cluster is unnecessary; land all
eleven fragments in one PR since they have no independent review value
split apart, but keep this PR free of any `SKILL.md`/generator code.

---

### P0.2 — Author the four P0 `SKILL.md` files — **done** (including P1.6's CI-wiring content, folded in during authoring)

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

**Dependencies:** P0.1 (shared fragments must exist to reference). P0.2 is
intentionally *not* blocked on P0.4 (`abicheck info`) or P0.5 (comparability
`reason_codes`) — those are independent product-surface PRs that can land
in parallel, and P0.2's own rule above ("no skill may reference a flag,
command, or report field that doesn't exist today") means the first version
of each `SKILL.md` correctly omits them. That leaves a real gap, though:
nothing in the plan as originally written integrated `info`/`reason_codes`
into the already-authored skills once P0.4/P0.5 landed. Closing it: each of
P0.4 and P0.5 carries its own small follow-up commit (not a new phase item)
updating whichever of the four `SKILL.md`/`references/` files benefit —
`native-release-compatibility` and `native-binary-compatibility-review`'s
tool-selection steps for `info`, the same two skills' comparability/
baseline-comparability steps for `reason_codes` — verified by P0.7's drift
tests actually exercising the new field/command once referenced, not left
implicit.

**PR boundary:** one PR per skill (four PRs) — each is independently
reviewable for its own decision-tree correctness, and a reviewer for
`native-release-compatibility` doesn't need to also review
`native-api-evolution`'s design-pattern catalog in the same pass.

---

### P0.3 — Generator: `skills-src/` → `.agents/skills/` — **done**

**Problem:** ADR-058 requires generated, self-contained output — no
symlinks, no hand-maintained copies — and requires each skill's
`references/` manifest to resolve only the `shared/` fragments that skill
actually cites.

**Change:** `scripts/gen_agent_skills.py` — for each `skills-src/<name>/`
directory: (1) parse its `SKILL.md` **and every file under its own
`references/`** (not `SKILL.md` alone — P0.2 explicitly allows
skill-specific `references/*.md` files, e.g.
`native-api-evolution/references/design-patterns.md`, and any of those can
just as legitimately link to a `shared/*.md` fragment as `SKILL.md`
itself; scanning only the top-level file would silently miss a fragment
referenced solely from a `references/` file, copying an incomplete
fragment set) for a references-manifest comment or explicit link list
identifying which `shared/*.md` fragments are used, and resolve this
**transitively** — a copied `shared/*.md` fragment can itself reference
another `shared/*.md` fragment, so keep resolving until a fixed point,
not just one pass; (2) copy the skill's own `SKILL.md` and `references/`
into `.agents/skills/<name>/` — "copy," not "verbatim": step (3) below
rewrites internal links in these same copied files, so treat this step as
"stage the source files at the output path," with link-rewriting a
required follow-on, not an unrelated later pass a naive implementation
could skip — then produce the same tree again at `.claude/skills/<name>/`
and `.gemini/skills/<name>/`
(a second output root from the same resolved and already-rewritten
content, not a second hand-authored copy — per ADR-058's "one additional
generated packaging target" rule) so this repository's own Claude Code
sessions have something to scan; (3) copy (not symlink) each cited
`shared/*.md` fragment (transitively resolved per above) into
`.agents/skills/<name>/references/shared/`, rewriting every referencing
file's internal link — `SKILL.md`'s, each `references/*.md` file's, and
each other copied fragment's — to the copied path; (4) fail loudly if any
scanned file references a `shared/` fragment that doesn't exist, or a
`shared/` fragment that exists but is never cited (directly or
transitively) by any skill (dead fragment — same "no orphaned content"
discipline as `changekind-detector`'s WARN check for orphaned
`ChangeKind`s). **A copied
fragment's own outbound links need the same treatment, not just the
skill's own — otherwise a self-containment gap survives the generator.**
Step (3)'s link-rewriting only covers the skill's own `SKILL.md`
referencing its now-local fragment copy; a `shared/*.md` fragment's own
body still links to canonical `docs/learn/`/`docs/use/`/`docs/reference/`
pages by design (that's the whole point of a summary-with-link fragment,
per design principle 6) — those repo-relative links resolve fine inside
this repository's own checkout, but not inside a skill installed
*standalone* through a marketplace or `skills.sh`, where `docs/` isn't
present at all. P0.7's structural tests only check that a target exists
*in this repo* (self-containment "no path outside the installed
directory" is checked at generation time, against the repo, not against a
standalone-install scenario), so this gap can pass every planned test and
still ship dangling references in a real standalone install. **This isn't
only a `shared/*.md` problem, either: a skill's own `SKILL.md` or
skill-specific `references/*.md` file can link directly to a canonical
`docs/` page too** — P1.6's CI-wiring links are exactly this case — so the
rewrite must run over every staged Markdown file the generator produces
(`SKILL.md`, every `references/*.md`, and every copied `shared/*.md`
fragment alike), not only the copied-fragment subset. Rewrite each staged
file's outbound canonical-doc links to the real published docs
site at generation time — read the host from `mkdocs.yml`'s own `site_url`
key rather than hardcoding the literal URL, so the generator (and its
tests, which must derive their expected output from that same `mkdocs.yml`
read, not a second hardcoded literal) don't go stale if the site ever
moves. `site_url` alone only supplies the host/path prefix, though — it
doesn't say how a repo-relative `docs/foo.md` maps to its published path,
so state that mapping explicitly rather than leaving it implied:
`mkdocs.yml` sets no `use_directory_urls` key today, so mkdocs's own
default (`true`, "directory URLs") applies — `docs/learn/foo.md` publishes
as `<site_url>/learn/foo/` (the `.md` extension dropped, a trailing slash
added, no `index`/`.html` in the visible path), and a page-relative anchor
(`docs/learn/foo.md#some-heading`) carries straight through as
`<site_url>/learn/foo/#some-heading` — mkdocs doesn't rewrite anchor text.
**Read `use_directory_urls` from `mkdocs.yml` itself (defaulting to `true`
only when the key is absent, exactly mirroring mkdocs's own default
resolution), not a hardcoded assumption that today's absence is
permanent** — freezing "no key means directory URLs" as a second,
un-synced fact owner would let the generator and its tests silently stay
green while emitting broken `/learn/foo/`-style links the day
`use_directory_urls: false` is ever set (which would publish
`docs/learn/foo.md` as `<site_url>/learn/foo.html` instead). The URL
construction must branch on the real, currently-configured value, not
assume the directory-URL shape unconditionally.
The generator's rewrite logic and its tests must apply exactly this rule,
and a test fixture should assert the produced URL for at least one real
multi-segment path (confirming both the extension-to-trailing-slash
mapping and anchor pass-through), not just that the host prefix matches.
Today `site_url` resolves to `https://abicheck.github.io/abicheck/`, the
same host `README.md` already links to throughout — the same class of fix
already used elsewhere in this plan for a
non-`docs/` link (P0.9's full-GitHub-URL convention) — a generated skill
file should never contain a bare relative link to something outside its
own installed tree. Modeled
directly on the existing `scripts/gen_cli_reference.py`
pattern (`docs/AGENTS.md`'s "regenerating
generated docs" contract) — same idempotency and same "verify.py fails on
drift" requirement, applied to a new artifact family.

**Files:** `scripts/gen_agent_skills.py` (new); `scripts/CLAUDE.md`
(add the new script to its inventory table in the same PR — the
`script-inventory` AI-readiness check warns on any `scripts/*.py` not
listed there, and a generator this load-bearing is exactly the kind of
maintenance entry point that check exists to keep discoverable);
`.agents/skills/**` (the authoritative generated output, committed) **and
`.claude/skills/**` and `.gemini/skills/**` (the generated packaging-target copies for this
repository's own dogfooded Claude Code use, per ADR-058's source-of-truth
model — also committed, not left as an untracked manual copy: without it,
Claude Code has nothing to scan in this repository at all, since it does
not read `.agents/skills`)**; `scripts/verify.py`
(new step, `agent-skills-generated`, wired into the `pr` profile alongside
the existing generated-doc regeneration checks — covering drift in both
generated trees, not just `.agents/skills/`).

**Tests:** `tests/test_gen_agent_skills.py` — idempotency (running the
generator twice produces identical output), dead-fragment detection,
missing-fragment-reference detection, no-symlinks-in-output assertion,
every generated `SKILL.md`'s internal links resolve inside its own
installed directory (the self-containment invariant, checked mechanically,
not just by design intent) — **run in both committed output trees,
`.agents/skills/**`, `.claude/skills/**`, and `.gemini/skills/**`, not only the first: P0.3
commits two publication trees from one generator, and a broken relative
link or unrewritten doc reference introduced only by the Claude-specific
emission path would otherwise pass every planned test indefinitely (drift
tests re-check the same faulty output each time, and regeneration staying
idempotent doesn't mean the output is correct) — either run this same
assertion set again over `.claude/skills/**` and `.gemini/skills/**`, or assert
each's generated content is byte-for-byte identical to
`.agents/skills/**`'s per-skill content and rely on the single check** ;
a separate assertion that no generated file
under either tree (including copied `shared/*.md` fragments, not
just each skill's own `SKILL.md`) contains a bare relative link outside
its own installed directory — every such link must be either
repo-relative-and-resolvable-inside-the-generated-tree, or already
rewritten to the published `abicheck.github.io/abicheck` docs host; a
dedicated fixture case for the indirect-reference gap above — a
skill-specific `references/*.md` file (not `SKILL.md`) linking to a
`shared/*.md` fragment that nothing else in that skill cites — asserting
the generator still discovers, copies, and correctly rewrites that
fragment; and a transitive-reference fixture (fragment A links to
fragment B) asserting both get copied.

**Dependencies:** P0.1, P0.2.

**PR boundary:** one PR — the generator and its first generated output
land together, since an ungenerated generator has no reviewable output.

---

### P0.4 — `abicheck info --format json` — **not started**

**CLI/API change:** yes — new root-level command.

**Problem:** No machine-readable way for a skill (or any agent) to ask
"what can this installed abicheck do" — confirmed absent from
`abicheck/cli*.py` in this plan's grounding pass. A skill deciding whether
a given root command (e.g. `project`, `scan`) exists on this installation
at all, or which extraction providers exist on this host, otherwise has to
parse `--version`'s human string or probe by trial-and-error, which both
`native-release-compatibility` and `native-binary-compatibility-review`'s
tool-selection steps need to avoid. **Scope, stated precisely so this
item isn't read as covering more than it does:** the payload below is a
command-*presence* inventory (which root commands and schema families
exist), not a per-command *option* inventory — it answers "does this
installation have `compare` at all," not "does this installation's
`compare` support `--contract`." A skill checking for a specific flag's
availability (e.g. `compare --contract`) still needs to probe or parse
`--help` for that narrower question; closing that gap would need a real
per-command option inventory in the payload, which is out of scope for
this item as specified.

**Change:** Add `abicheck info` (small, read-only, no operands) emitting
JSON: `abicheck_version`, and a `schema_versions` map covering **every
artifact family** `abicheck/schemas/__init__.py`'s `current()` registry
exposes — currently `snapshot`, `compare`, `scan`, `aggregate`,
`build-output`, `run-plan`, and (once P0.5's dependency-stack schema
addition lands, independently landable in either order) `stack`. This
plan deliberately does **not** pin a specific count (not "six," not
"seven") anywhere in `info`'s described payload or tests, precisely
because P0.5 is an independent P0 item that changes the true count — a
hard-coded number here would contradict P0.5 the moment either item lands
first. The three-field version limited to snapshot/compare/scan an
earlier draft of this item proposed was an oversight, not a deliberate
scoping — `native-release-compatibility`'s documented `project plan`/
`aggregate` workflow needs `aggregate`/`build-output`/`run-plan` versions
too, and without them the skill would have to fall back to probing or
parsing output for exactly the thing this command exists to answer
directly. `_ARTIFACT_NAMES` (the catalog `current()` validates against) is
a private module-level constant today — `cli_info.py` iterating it
directly would mean importing a private symbol, and a later artifact
family registered only in `current()`'s internals without a matching
public export could silently fall out of `info`'s payload. Add a small
public accessor (e.g. `schemas.artifact_names()` returning the frozenset,
or a `schemas.all_current()` returning the whole `{name: version}` map
directly) that `cli_info.py` calls instead of touching `_ARTIFACT_NAMES`,
and test `info`'s payload **solely** against that public accessor's live
output — and assert the **values**, not only the key set: a key-only
assertion (`info_payload["schema_versions"].keys() ==
schemas.artifact_names()`) still passes if `info` returns a stale or
hard-coded version string for a real key, which is exactly the failure
mode a discovery command exists to prevent. Assert the full mapping
instead — `assert info_payload["schema_versions"] == schemas.all_current()`
(or `{name: schemas.current(name) for name in schemas.artifact_names()}`
if only the narrower accessor exists) — never against a hand-copied name
list, count, or per-family version literal of any size, so this test
can't itself go stale the same way the "all six" framing would have.
**The same discipline applies to the root command list, not only to
schema versions** — an earlier draft of this item left the command-list
test unspecified, which would let it silently become a second,
hand-maintained fact owner (a stale list that stops tracking a newly
added or removed root command while every other part of this test stays
green). The command list must be derived from the live CLI the same way
`scripts/gen_cli_reference.py` already does (`click` introspection over
`main`'s registered commands, the same mechanism P0.7's tool/API drift
tests reuse), and the test must assert exact equality against that live
introspection, not a hand-copied list. Also included in the payload:
available extraction providers (castxml/clang presence,
detected on the host the same way existing dumper-provider auto-detection
already probes), and platform capabilities (ELF/PE/Mach-O support — all
three ship unconditionally today, but this keeps the field meaningful if
that ever changes).

**Admission-bar status: does not clear criterion 2 as literally worded,
and this plan does not pretend otherwise.** Checked against `AGENTS.md`'s
"Adding a new top-level command" admission bar (ADR-054 D6): criterion 2
("its operand is a domain object a user already thinks in terms of") is
not met — `info` takes no operand at all, and
none of the domain objects the bar's own examples name (a binary, a set of
reports, a project config) apply. The `--version` comparison an earlier
draft of this item leaned on does not actually resolve this: `--version`
is an eager `click.version_option` **flag** on the `main` group, not a
root **command**, so it was never subject to this bar in the first place
and is not a valid precedent for clearing it. **This item is therefore
blocked on an explicit, upfront maintainer decision before implementation
starts** — not "flag it for discussion in the PR that adds it," which
treats a real gap as a formality to be waved through post hoc. Two
legitimate paths, either acceptable, neither assumed here: (a) the
maintainer grants `info` an explicit, documented exception to criterion 2
(recorded in `AGENTS.md` itself, alongside the bar, so it doesn't
re-surface as an apparent contradiction for the next command that tries
the same argument); or (b) `info` is redesigned to avoid the conflict
entirely — e.g. as a genuine extension of `--version` (replacing the
built-in `click.version_option` with a custom eager callback that supports
`--format json`, keeping it a flag rather than a new command) rather than
a new root verb. This plan does not pick between them; that choice needs
the maintainer, not an implementer's judgment call at merge time. Once
resolved, whichever design ships lands with `tests/test_cli_root_surface.py`
+ `AGENTS.md` + `README.md` + generated CLI reference updated in the same
PR per the bar's own sixth criterion (if path (a) is chosen) or the
equivalent flag-level tests (if path (b) is chosen).

**Files, conditional on which design the maintainer decision above
selects — the two paths are not interchangeable file lists, so don't apply
both:**
- **If path (a), a new `info` root command:** `abicheck/cli.py` is already
  1,980 of the AI-readiness gate's 2,000-line hard cap — only ~20 lines of
  headroom — and `info`'s own body (Click registration, `--format`
  handling, schema-version discovery via `schemas.all_current()`, provider
  detection, platform-capability probing, JSON/text rendering) is not a
  20-line function. `AGENTS.md`'s "small command, no significant helpers"
  convention still governs *where the `@main.command(...)` registration
  itself* lives, but landing `info` here needs a same-PR extraction that
  shrinks `cli.py` first — moving an existing self-contained slice of it
  into a sibling module the way prior commands already have (see the
  module map's `cli_<name>.py` precedent) — so the command lands with
  headroom instead of being the change that trips the hard cap; `abicheck/
  cli.py` (the `info` command itself, post-extraction); `tests/
  test_cli_root_surface.py` (extend the pinned root-command-set assertion
  to include `info`); `README.md` ("Which command do I need?" table gets
  one more row); `docs/reference/cli-reference.md` (regenerated).
- **If path (b), extending `--version --format json`:** `abicheck/cli.py`
  — a naive "replace `click.version_option` with a custom eager callback
  that also reads `--format`" does not work, and neither does making both
  flags eager or both non-eager: per Click's documented callback evaluation
  order, parameters within the *same* eagerness group are still processed
  in command-line order, so `abicheck --version --format json` (`--version`
  typed first) fires the version callback first regardless of whether both
  flags are eager or both are non-eager — `ctx.params["format"]` still
  isn't populated yet either way. **Mixed eagerness (`--format` eager,
  `--version` non-eager) — this item's own earlier revision — has a
  separate, worse problem: it isn't just a `--version`-scoped flag, it's a
  real root-level `--format` option Click resolves independently of any
  subcommand's own option of the same name.** Confirmed against the live
  CLI (`python -m abicheck --help` exposes only `--version`/`--help` at
  root today; `python -m abicheck compare --help` defines its own
  `--format`, "Output format," as a `compare`-scoped option) — registering
  a root `--format` would make `abicheck --format json compare OLD NEW`
  silently consume the root value while `compare`'s own `--format` stays at
  its text default, a real correctness regression for every other command,
  not just a `--version`-adjacent quirk. **A follow-up correction to this
  same item: an even earlier revision proposed avoiding a declared option
  entirely — manually parsing `--format` out of `ctx.args`/`sys.argv`
  inside `--version`'s own callback instead. That doesn't work either
  (confirmed against real Click 8.3.3, both flag orders): Click's parser
  rejects any *undeclared* option with "No such option: --format" during
  argument parsing itself, before any callback runs at all — `ctx.args`
  isn't even populated with the leftover token by that point, since Click
  errors out first. `--format` must be a properly declared Click option,
  not something recovered from raw argv.** The design that actually works
  within Click's real constraints: declare `--format` as a normal, eager
  root-level option (exposed value, no rejecting logic of its own), and
  put the enforcement in `--version`'s own (non-eager) callback instead —
  since eager parameters are always processed as a whole group before any
  non-eager one, `ctx.params["format"]` is guaranteed populated by the
  time `--version`'s callback runs, regardless of argv order (this part of
  the mixed-eagerness reasoning above is still correct; only the "avoid a
  declared option" idea was wrong). Inside `--version`'s callback: if
  `--version` itself was not passed (`not value`) but `--format` was
  (`ctx.params.get("format") is not None`), raise a `click.UsageError`
  ("`--format` is only valid together with `--version`") instead of
  silently continuing — this is what actually closes the original
  shadowing gap: `abicheck --format json compare OLD NEW` now fails loudly
  with a clear message pointing at the mistake, rather than either
  erroring on an undeclared option or silently consuming the value with no
  effect. When `--version` *is* passed, its callback renders using
  `ctx.params["format"]` and exits, exactly as the mixed-eagerness section
  above describes. Add a regression
  test asserting a non-`--version` invocation
  (`abicheck --format json compare OLD NEW` or similar) raises this
  `UsageError` rather than either erroring on an unknown option or
  silently succeeding with the wrong format. Test the documented `abicheck
  --version
  --format json` spelling itself (both flag orders — `--format json
  --version` too), not just each flag in isolation; no root-command
  surface changes at all, so
  `tests/test_cli_root_surface.py`'s pinned set is untouched (adding `info`
  there would assert a command that was never created) and `README.md`'s
  command table gets no new row — this design's only surface change is to
  `--version`'s own existing flag-level behavior/tests.

**Tests, conditional on the same design choice as the Files list above —
don't apply both:**
- **If path (a):** `tests/test_cli_info.py` — JSON shape, schema-version
  values match `serialization.SCHEMA_VERSION`/the report-schema constant
  at import time (no hand-copied duplicate numbers), provider-detection
  reflects the actual test-environment tool availability.
- **If path (b):** no `info` command exists, so `tests/test_cli_info.py`
  is not created — following the path (a) test checklist here would test
  a surface that was never built, or worse, prompt implementing `info`
  anyway and silently reintroducing the root command path (b) exists to
  avoid. The equivalent coverage (schema-version values, provider
  detection) instead lives on `--version --format json`'s own test file,
  asserting the same content the path (a) tests would have, against the
  actual flag surface path (b) built.

**Docs:** `README.md`, `docs/reference/cli-reference.md` (generated),
changelog fragment (`changelog.d/`, `### Added`).

**Dependencies:** none — independent of the skill-authoring track, can land
in parallel with P0.1–P0.3.

**PR boundary:** one PR, product-surface only — no skill-content changes
bundled in. A small separate follow-up PR (whenever P0.2's skills already
exist) updates the relevant `SKILL.md`s to consume whichever
capability-discovery surface the maintainer decision above actually
selected — `info` under path (a), or `--version --format json` under path
(b); path (b) has no `info` command at all, so a follow-up written against
path (a)'s spelling unconditionally would reference a command that was
never built — see P0.2's Dependencies note.

---

### P0.5 — Finer-grained `reason.codes` on the existing not-comparable object — **not started**

**CLI/API change:** yes — additive report-schema field.

**Problem:** A `not_comparable` result already renders as a top-level
`"reason": {"kind": ..., "message": ...}` object in the `--format json`
document (per `abicheck/schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` —
not restated as a literal here, since it's a volatile fact owned there,
not in this plan; `cli_compare_helpers._report_not_comparable`), but
`kind` today is only ever `"profile_mismatch"` or `"scope_mismatch"` — two
coarse buckets. The *specific* mismatched field(s) (compiler family vs.
compiler version vs. language standard vs. ...) are only recoverable from
the free-text `message`, so a skill (or any caller) branching on the real
cause has to parse prose. `native-binary-compatibility-review`'s "establish
comparability" workflow step and `native-release-compatibility`'s
"baseline comparability" step both need a typed reason instead.

**One canonical owner for the enum itself, checked against every schema
that republishes it — not just one of the several.** This item's `codes`
values are reused, by name, across six distinct producer surfaces
(`compare`, `scan`, the release JSON, the release
summary, `aggregate`, and `deps compare`/stack — the CLI command and the
report shape it emits (`stack_checker.py`/`stack_report.py`), counted here
as one producer since they share a single new schema below) and — once
this item's own schema
work above lands — **multiple** JSON Schemas: `compare_report.schema.json`
plus the new `scan_report.schema.json`, the release-report schema(s), and
`stack_report.schema.json`, each independently republishing the same
closed enum in its own `reason.codes`/`reason_codes` field definition. A
sync test scoped to `compare_report.schema.json` alone (an earlier draft
of this note's own scope) would stay green while any of those other
schemas silently drifted from the Python source of truth — a new enum
member added there would make `scan`/`release`/`stack` output the schema
itself claims to reject, undetected. Two ways to close this, either
acceptable: (a) have every one of those JSON Schema files `$ref` **one**
shared schema fragment for the enum (e.g. `abicheck/schemas/
_reason_code_enum.schema.json`) instead of each repeating the `enum:`
list inline, so there is only one JSON-side copy to keep in sync with the
Python enum in the first place; or (b) if a shared `$ref` isn't practical
for a given schema's structure, the schema-sync test below must
explicitly enumerate and check **every** report schema that republishes
the enum, not just `compare_report.schema.json`. Define the enum
**once**, at the Python level — a single `ComparabilityReasonCode` enum
(or equivalent frozen mapping) in `abicheck/comparability.py`, since
that's where the values are actually derived — and have every producer
above import and emit from that one definition, never a hand-copied
string. Add a schema-sync test (`tests/test_comparability_gate.py` or
`tests/test_report_schema_receipt.py`) that walks the Python enum's
members and asserts each one is present in **every** applicable schema's
`enum:` list (or the shared `$ref` fragment, under option (a)), and vice
versa for each — the same "one fact, one place, checked automatically"
pattern this repo already applies to other registries (e.g. `ChangeKind`
vs. its JSON Schema representation), so adding or renaming a reason code
fails
loudly in this one test instead of silently producing schema-invalid tool
output or letting one producer drift from another.

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
known, stable, independently-mismatching field — **but see the fingerprint-
gate bug noted below, which must be fixed for this code to ever actually
fire**), `comparability.py`'s separate `dependency_scope` mismatch kind
(`_check_dependency_scope_comparable`/`ComparabilityMismatch.kind ==
"dependency_scope"` — a distinct category from `"scope"`/`"profile"`
already modeled in code, raised when one snapshot was extracted with
dependency filtering and the other with `--include-dependencies`; give it
its own `dependency_scope_mismatch` code rather than folding it into
`other_scope_mismatch`, since the underlying code already treats it as a
first-class, separately-diagnosable case), and `SCOPE_FIELD_KEYS`/
`_MANIFEST_SCOPE_FIELD_KEYS` (`headers`, `public_header_dirs`,
`translation_units`) can each independently contribute, plus one explicit
code per exception category already carved out in `comparability.py`
(`_PLATFORM_IDENTITY_FIELDS`, `_BUILD_CONTEXT_FIELDS`) and one
`other_profile_mismatch`/`other_scope_mismatch` catch-all for any field not
individually enumerated — so a future `PROFILE_FIELD_KEYS`/
`SCOPE_FIELD_KEYS` addition degrades to a generic-but-still-typed code
instead of silently emitting nothing.

**Pre-existing gate bug this item must fix, not just work around:**
`check_contracts_comparable`'s fingerprint-authenticity re-verification
(`_fingerprint_matches_fields(..., PROFILE_FIELD_KEYS)`) always checks
against the 11-key `PROFILE_FIELD_KEYS`, even for a DPC++ contract whose
`profile_fingerprint` was originally hashed over the 12-key
`_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS` (the frontend-aware key set
`compute_extraction_contract` selects for a DPC++-capable frontend). That
mismatch means a DPC++ pair's fingerprint re-verification fails the
authenticity check *before* the actual per-field `differing` computation
(which does correctly use `_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`) ever
runs, so today every DPC++ profile mismatch raises the generic "fields do
not reproduce fingerprint... cannot be verified safe" path — the dedicated
`frontend_context_kind_mismatch` code above is unreachable until this gate
itself is fixed to authenticate against the correct key set for a DPC++
contract. Fix `_fingerprint_matches_fields`'s key-set selection at both
authenticity call sites (mirroring the same
`_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`-when-DPC++ branch
`compute_extraction_contract` already uses) as part of this item.

**Fixing authentication alone is not sufficient — the downstream
classification step has the identical narrow-key-set bug, one level
further in.** Even once authentication correctly passes for a DPC++ pair,
`unknown_differing` (`comparability.py`, the computation that decides
which differing keys are "known, typed" vs. "unrecognized") still checks
`k not in PROFILE_FIELD_KEYS` — never `_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`
— so `frontend_context_kind` would still be classified as an unknown
field and fall into the generic `other_profile_mismatch` fallback instead
of getting its own dedicated code, exactly the outcome this whole item
exists to avoid. Extend the same widened/per-side key-set selection
through `unknown_differing`'s own computation, not just the
`_fingerprint_matches_fields` authenticity calls — both are needed for
the dedicated code to actually reach a caller. Assert the *actual*
emitted code (not just that a code exists, and not just that
authentication no longer raises) in the DPC++ regression test.

**The identical bug pattern exists on the scope side too, one level
lower — fix both, not just the profile/DPC++ instance.** The scope
fingerprint's own authenticity re-verification (`_fingerprint_matches_
fields(..., SCOPE_FIELD_KEYS)`, `check_contracts_comparable`'s scope-side
check) always re-hashes against the plain `SCOPE_FIELD_KEYS`, even when
either side's contract came from a manifest-driven dump, whose
`scope_fingerprint` `compute_extraction_contract` hashes over the wider
`_MANIFEST_SCOPE_FIELD_KEYS` (`SCOPE_FIELD_KEYS` plus
`translation_units`). A manifest-sourced scope mismatch therefore fails
authenticity before the real per-field diff ever runs, for the same
reason the DPC++ profile case does — meaning the `translation_units`
mismatch code above is equally unreachable until this scope-side
authenticity check is fixed too. **Select the key set independently per
side, not once for the whole check** — each `_fingerprint_matches_fields`
call authenticates one side's own fingerprint against that same side's
own fields, so a "use `_MANIFEST_SCOPE_FIELD_KEYS` when *either* side is
manifest-derived" rule (an earlier draft of this note's own wording) is
itself wrong: when only one side is manifest-derived, applying the wider
key set to the *other*, legacy-hashed side would re-hash it with a field
(`translation_units`) its own fingerprint was never computed over, so
that side's authenticity check would newly fail instead of newly pass.
Each of the two `_fingerprint_matches_fields` calls (old side, new side)
must independently decide `_MANIFEST_SCOPE_FIELD_KEYS` vs.
`SCOPE_FIELD_KEYS` from *that side's own* contract, mirroring how
`compute_extraction_contract` itself decides the key set once per
snapshot, never once per comparison.

**Same downstream-classification gap as the profile side, here too.**
`scope_unknown_differing`'s own computation checks `k not in
SCOPE_FIELD_KEYS`, never `_MANIFEST_SCOPE_FIELD_KEYS` — so even once
authentication is fixed, a manifest pair's `translation_units` difference
would still be classified as unknown and fall into
`other_scope_mismatch`, not its own dedicated code. Extend the same
per-side widened key selection through `scope_unknown_differing` too, not
only the two authenticity calls. Add a manifest-scope-mismatch regression
test asserting the actual emitted `translation_units`-related code, **plus
a mixed-sides case** (one manifest-derived, one legacy) to catch exactly
this per-side-vs-whole-check distinction — correcting only the
DPC++/profile call sites (both authentication *and* classification) and
leaving these scope-side ones untouched would leave this one code equally
unreachable. Derived from the same field-by-field
comparison `check_contracts_comparable`/`compute_extraction_contract`
already perform when raising `ProfileMismatchError`/`ScopeMismatchError` —
promotes existing internal evidence to a stable public field, adds no new
detection logic.

**Explicit scope boundary, not silently overclaimed:** the "every
simultaneous cause" completeness promise above holds *within* whichever
single exception the gate actually raises — every differing field the
raised `ProfileMismatchError`/`ScopeMismatchError` itself carries, all of
them, never truncated to one. It does **not** span both exception domains
at once: `check_contracts_comparable`'s gate is a sequence of checks with
early-return control flow (a dependency-scope/scope-fingerprint mismatch
is raised and returns before the later profile-fingerprint check ever
runs), so a pair that differs on *both* scope and profile fields still
surfaces only the scope-domain `codes` on the first run — a caller fixing
that and re-running can still hit a profile-domain mismatch the first
`codes` array never mentioned. Closing that residual gap would mean
restructuring the gate to collect every domain's mismatches before
raising, which is a real, separate design change to `comparability.py`'s
control flow, not a report-field addition — out of scope for this item.
Document this boundary explicitly wherever the enum is documented (not
just in this plan), and scope P0.5's tests to match: single-domain
multi-field cases assert full completeness within that domain; do not add
a cross-domain (scope-and-profile-simultaneously) completeness test, since
that is not what this item delivers. Report-schema version bump per the
existing
`REPORT_SCHEMA_VERSION` convention (ADR-047 §7 / ADR-055 D3's registry).

(The MCP server's `abi_compare` tool once needed its own, separate
treatment here, since its `not_comparable` envelope emitted `"reason"` as
a bare string rather than an object — moot now that #684 removed the MCP
server entirely; see this plan's Amendment note above.)

**Files:** `abicheck/errors.py` (`ProfileMismatchError`/
`ScopeMismatchError` are actually *defined* here, not in
`comparability.py` — that module only imports and raises them; their
constructors/data contract need to gain the structured mismatched-field(s)
attribute here, so `codes` can be derived without re-parsing the message,
and so a multi-field mismatch keeps every field, not just the first one
hit), `abicheck/comparability.py` (the raising logic — `check_contracts_
comparable`'s call sites pass the structured fields into the now-extended
exception constructors, and derive `codes` from them for
`_report_not_comparable`, etc. — stays here, unchanged in location),
`abicheck/schemas/compare_report.schema.json` (the
`reason` object's fields are defined in the JSON Schema file itself, not in
`abicheck/schemas/__init__.py` — that module only holds version constants
and registry lookups; registering `codes`' closed enum here is what lets
P0.7's schema-based drift check recognize the new field at all, not an
optional extra), `abicheck/cli_compare_helpers.py`
(`_report_not_comparable` emits `codes`), `abicheck/cli_compare_release.py`
(its own `"reason": {...}` construction site, written under
`--output-dir`, gets the same field — **and** this module, not
`cli_compare_release_helpers.py`, is where `_compare_one_library` and
`_write_release_summary_file` are actually defined; only
`_format_release_json` lives in the helpers module. Both real owners need
the change: `_compare_one_library`/`_write_release_summary_file` here in
`cli_compare_release.py`, `_format_release_json` in
`cli_compare_release_helpers.py` — get this split wrong and the compact
`summary.json` writer's schema-version marker/`reason_codes` field is
exactly the piece left unimplemented), `abicheck/cli_compare_release_helpers.py`
(`_format_release_json` — the *primary* release report path, distinct from the separate
not-comparable document above: today a not-comparable library in a
directory/package comparison returns only `"reason": str(exc)` into the
entry `summary.json`/the release JSON actually expose, so without this
file `native-release-compatibility` — the skill this field mainly exists
for — would still see untyped text in the report it actually reads.
**Neither of these two release-summary outputs carries a schema version
marker or a registered JSON Schema today, and they are two genuinely
different shapes, not one** — confirmed: no `abicheck/schemas/
release_report.schema.json`/`release_summary.schema.json` exists, neither
function emits a version field, and `_format_release_json()`'s payload
(`old_dir`/`new_dir`/`changed_libraries`/`warnings`, optional severity/
matrix blocks) is structurally distinct from `_write_release_summary_file()`'s
compact `verdict`/`libraries`/`unmatched_old`/`unmatched_new` envelope —
so, same as the `scan`/`deps compare` cases, a bare `reason_codes`
addition described as "one vague schema" would leave one of the two
shapes unvalidated. Give the release JSON/summary their own schema
version constant(s) and a registered JSON Schema covering **both**
shapes explicitly — either two distinct schema families, or one schema
with explicit `oneOf` branches for the two envelopes — plus a published
mirror and real-output validation tests for **both** `_format_release_json()`
and `_write_release_summary_file()` output, not just one of the two, as
part of this same change, not deferred), `abicheck/scan_engine.py` (`scan --against` catches the same
two exceptions at its own comparability gate, line ~1039, and today emits
only `diff_summary = {"reason": str(exc)}` — free text, same gap as the
`compare` path this item was originally scoped to; add the same
`reason_codes` field here too, since `scan`'s CLI surface shares
this one engine and would otherwise still have to parse prose for exactly
the causes this item exists to type. **`SCAN_SCHEMA_VERSION` is a version
marker with no schema behind it, and it marks three distinct shapes, not
one** — confirmed: no `abicheck/schemas/scan_report.schema.json` exists
(only `aggregate_report`, `build_evidence`, `build_source_pack`, and
`compare_report` do); `ScanOutcome.diff_summary` (`scan_engine.py`) is
typed as plain `dict[str, Any]`; and the same version constant is also
stamped onto `ScanResult.to_dict()` and `ScanSetResult.to_dict()`
(`service_scan.py`) — the service single-scan envelope and the
multi-artifact scan-set envelope, both structurally different from
`ScanOutcome`'s own shape and from each other. Testing only `scan
--against`'s CLI output (as an earlier draft of this item implied) would
leave the other two shapes free to claim the same schema version without
ever being validated against it. Add the actual `scan_report.schema.json`
and its published mirror as part of this change, either as one schema
with explicit `oneOf` branches for the three shapes or as distinct schema
families each with their own version — and cover all three with real
validation tests, not just `scan --against`'s, `abicheck/aggregate.py`
(`TargetReport.reason` is itself a bare `str | None` — `_load_report_file`
reads a per-target report's structured `reason: {kind, message}` object
and flattens it straight into that one string field, line ~1471 — so
`native-release-compatibility`'s documented multi-profile/multi-target
`project`/`aggregate` path would still be untyped prose even after every
producer above emits `codes`; add a `reason_codes: list[str] | None` field
to `TargetReport` alongside `reason`, carried through from the per-target
report's `codes`, and expose it from `TargetReport.to_dict()` /
`abicheck/schemas/aggregate_report.schema.json`. `_load_report_file`
today only recognizes the compare-shaped top-level `verdict: null` +
`reason: {...}` object — a `scan --against` not-comparable report is
shaped differently (`verdict: "NOT_COMPARABLE"`, reason nested under
`diff`), so this extraction needs its own scan-specific branch reading
`diff.reason_codes`, not just the compare-shaped path, or a `project`
matrix backed by `scan` targets would still silently lose the field;
bump `AGGREGATE_SCHEMA_VERSION` in `abicheck/aggregate.py` for this
additive key, independently of the compare/scan report schema bumps —
it's its own versioned contract, not implicitly covered by bumping
theirs), `abicheck/stack_checker.py`
(`StackChange.not_comparable_reason` is likewise a bare `str | None` —
`deps compare`'s own comparability gate; add a sibling
`not_comparable_reason_codes` field) and `abicheck/stack_report.py`
(`stack_to_json()`'s `sc_dict["not_comparable_reason"]` emission gets the
same sibling field) — `deps compare --format json` is another
machine-readable comparability entry point this item's "a skill can branch
on one field" goal applies to equally, and is the natural backend for a
future dependency-floor/runtime-upgrade skill, so it shouldn't be left
behind the other four producers. **Stack JSON output has neither a schema
version marker nor a registered JSON Schema at all today** — confirmed: no
`schema_version` field/constant and no `abicheck/schemas/
stack_report.schema.json` anywhere in `stack_checker.py`/`stack_report.py`/
`abicheck/schemas/` — so a version constant alone would not be sufficient:
P0.7's schema-based drift check validates against an actual field
contract, and a bare version number with nothing to check it against gives
that check nothing to validate `not_comparable_reason_codes` against.
Deliver the full triple for `deps compare --format json` as part of this
same change (not deferred): (1) a `stack_schema_version` constant and
top-level field in `stack_report.py`'s output, (2) a real
`abicheck/schemas/stack_report.schema.json` defining the actual output
shape (modeled on `compare_report.schema.json`'s own structure) including
`not_comparable_reason_codes`, and (3) its published mirror under
`docs/reference/schemas/` via the existing `scripts/publish_schemas.py`
step, plus a schema-validation test asserting real `stack_to_json()`
output actually validates against it — not just that the version constant
exists, `abicheck/schemas/__init__.py`
(schema version bump + field registration, covering the compare, scan,
release-report, and dependency-stack report schemas — **and registration
in the public `_ARTIFACT_NAMES`/`current()` catalog for every new family
added here**, release-report included: P0.4's `abicheck info` discovery
payload is derived exclusively from that one registry, so a schema this
item adds but doesn't also register there stays invisible to the exact
capability-discovery surface this whole initiative exists to support —
covered by the same live-catalog test P0.4 already commits to, not a
separate hand-maintained list), `docs/reference/change-kinds.md` or a new
`docs/reference/comparability-reason-codes.md` (document the closed enum —
new page only if it doesn't fit as a section of an existing comparability
doc; check `docs/use/contract-evaluation.md` and `docs/reference/
compatibility-evaluation-config.md` first per `docs/AGENTS.md`'s "extend an
existing canonical owner" rule before creating one).

**Tests:** `tests/test_comparability_gate.py` (extend with `codes`
assertions covering every `PROFILE_FIELD_KEYS`/`_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`/
`SCOPE_FIELD_KEYS` entry, each exception carve-out, and the fallback code
for an unrecognized field — one single-cause case each, **plus** at least
one dedicated multi-field-mismatch case asserting `codes` contains every
simultaneously-mismatched field, not just the two current coarse kinds and
not just single-cause cases); `tests/test_report_schema_receipt.py`-style
version-bump check if one exists for this schema family; a
`tests/test_cli_scan.py`/`tests/test_scan_compare_parity.py`-style test
asserting `scan --against`'s own `diff_summary` gains the identical
`reason_codes` for the identical mismatch `compare` reports, so `scan` and
`compare` stay in parity on this field the way ADR-055 already requires
them to for everything else routed through the shared engine; a
`tests/test_cli_compare_release.py`-style test asserting a not-comparable
library's entry in the directory/package release JSON/`summary.json`
(`_compare_one_library`/`_format_release_json`) carries `reason_codes` too,
not just the separate not-comparable document — this is the report path
`native-release-compatibility` actually reads, so it's the one that must
not be left with only untyped text; two `tests/test_aggregate.py`-style
tests asserting `TargetReport.reason_codes` round-trips a not-comparable
per-target report's `codes` through `_load_report_file` into
`TargetReport.to_dict()`'s output — one for a compare-shaped per-target
report (`verdict: null` + `reason: {...}`) and one for a scan-shaped one
(`verdict: "NOT_COMPARABLE"`, reason nested under `diff`), since the two
are extracted by different code paths and only covering one would leave a
`scan`-backed project matrix silently losing the field; a
`tests/test_deps_compare.py`-style test asserting `stack_to_json()`'s
not-comparable output carries `not_comparable_reason_codes` alongside the
existing prose field.

**Docs:** whichever comparability doc the field lands in, plus a
changelog fragment (`### Added`).

**Dependencies:** none — independent of P0.1–P0.4, can land in parallel.

**PR boundary:** one PR, product-surface only. A small separate follow-up
PR (whenever P0.2's skills already exist) updates
`native-release-compatibility`/`native-binary-compatibility-review`'s
comparability-related workflow steps to use `reason_codes` — see P0.2's
Dependencies note.

---

### P0.6 — AI-readiness gate coverage for the new tree — **done**

**Problem:** Three separate gaps, all closed here rather than left
unregistered: (1) `skills-src/` is a new major sub-tree that silently falls
outside `claude-md-coverage` without registration; (2) `.agents/skills/`
(fully generated) needs an explicit "don't hand-edit" marker convention so
a contributor doesn't patch generated output directly, the way
`generated-file-ownership` already prevents for `docs/reference/examples/
case*.md`; (3) P0.1's `skills-src/shared/*.md` fragments each summarize a
real `docs/learn/`/`docs/use/`/`docs/reference/` canonical page, but
without registering that claim somewhere machine-checked, the summary can
silently drift from its source with nothing to catch it — links staying
valid (P0.7 checks that) is not the same guarantee as the *summarized
semantics* staying current.

**Change:** For (1)/(2): add `skills-src/CLAUDE.md` (scoped context, not an
`@AGENTS.md` adapter, per this repo's own convention — see this file's own
`AGENTS.md`/`CLAUDE.md` split as the model); add `skills-src` to
`REQUIRED_CLAUDE_MD_DIRS` in `scripts/check_ai_readiness.py`; add a
"this file is generated by `scripts/gen_agent_skills.py` — do not hand-edit"
marker comment convention to **every generated Markdown file under both
committed output trees, `.agents/skills/**`, `.claude/skills/**`, and
`.gemini/skills/**`** —
not just each skill's own top-level `SKILL.md` in either tree, but also
every copied skill-specific `references/*.md` file and every copied
`shared/*.md` fragment, since all of them are equally hand-edit-unsafe
generator output and the ownership check exists to cover exactly that
class of file, not one arbitrarily narrower slice of it, and a file under
`.claude/skills/**`/`.gemini/skills/**` is no less generator-owned than its `.agents/skills/**`
counterpart once P0.3 commits both. **This needs a real code change to
`check_generated_file_ownership()`, not just a registry entry — call this
out explicitly, the same way P0.1's `docs_contract.py` extension is called
out below.** `GENERATED_FILE_MARKERS` today is a flat list of exact
`(path, marker, generator)` tuples — it has no glob/tree-scan capability,
which is exactly why `docs/reference/examples/case*.md` is already handled
by a *second*, hardcoded glob loop in the same function rather than by
adding entries to that list one per case file. An arbitrary-depth,
arbitrary-count tree of per-skill generated Markdown is the same shape of
problem, at larger and growing scale (every skill's `SKILL.md` plus every
`references/*.md` plus every copied `shared/*.md`, times two trees) — so
add a third loop to `check_generated_file_ownership()` that walks
`.agents/skills/**/*.md`, `.claude/skills/**/*.md`, and
`.gemini/skills/**/*.md` directly (mirroring
the existing `examples/case*.md` glob loop's shape, not registering
individual paths in `GENERATED_FILE_MARKERS`), flagging any file under
either root missing the marker. For (3):
**don't invent a new bespoke drift checker** — register each
`skills-src/shared/*.md` fragment in `docs/_meta/topics.yaml` as a
`task_pages`/`allowed_summaries` entry against the topic(s) it summarizes
(the existing pilot registry already models exactly this "summary page
citing a canonical owner" relationship for `getting-started.md` and
friends), and give each fragment the `summarizes:` front-matter field
naming those topic ids. `scripts/check_docs_contract.py` — already wired
into `scripts/verify.py --profile pr` as the `docs-contract` step — then
enforces the round-trip that already exists for every other page in this
registry (every `summarizes` entry must be backed by a real, existing
topic; a page can't claim to summarize a topic it isn't registered
against) *for free*, with no new checking logic. This doesn't detect
prose-level semantic drift line-by-line (`check_docs_contract.py` doesn't
do that for any existing page either), but it does mean a `shared/*.md`
fragment can never point at a stale/renamed/nonexistent topic without
failing the same gate every other doc page already goes through — the
realistic bar this repo already holds itself to, not a stronger one
invented just for this new tree.

**This needs one small, scoped extension to `check_docs_contract.py`
itself, not just a registry entry — call this out explicitly rather than
assume it "just works".** Today `task_pages`/`allowed_summaries` entries
are checked with `_is_file_under(DOCS, ...)`, which hard-requires the path
to resolve inside `docs/`; `skills-src/shared/*.md` lives outside that
tree by design (it's the DRY source `.agents/skills/` is generated from,
not a published doc page), so registering it there as written would fail
the registry's own existence check, not pass it. `fact_sources` already
tolerates a non-`docs/` repo-relative path (code files are a normal
`fact_sources` entry today), so the fix is narrow: extend
`task_pages`/`allowed_summaries` validation to accept a repo-relative path
outside `docs/` the same way `fact_sources` already does, rather than
inventing a fourth path-resolution rule. That alone is not sufficient,
though: `_check_front_matter_schema()` — the function that actually reads a
page's `summarizes:` front matter and round-trips it against its
registered topic — iterates exclusively over `DOCS.rglob("*.md")` and
computes docs-relative identities from that; a `skills-src/shared/*.md`
fragment is invisible to it regardless of how permissive the registry
validation above becomes, so its `summarizes` claim would sit in
`topics.yaml` unchecked. Extend this function's scan (and its path-identity
computation) to also walk the registered non-`docs/` fragment paths, not
just `DOCS.rglob`. Land both extensions as their own small commit in
`scripts/check_docs_contract.py` before registering any
`skills-src/shared/*.md` entry, so the registration is provably checked
from the day it's added, not merely asserted to be.

**Files:** `skills-src/CLAUDE.md` (new); `scripts/check_ai_readiness.py`
(`REQUIRED_CLAUDE_MD_DIRS`; a new glob-based loop in
`check_generated_file_ownership()` for both skill trees, not a
`GENERATED_FILE_MARKERS` entry — see above); `scripts/
gen_agent_skills.py` (emit the marker — depends on P0.3);
`scripts/check_docs_contract.py` (extend `task_pages`/`allowed_summaries`
path validation to accept a repo-relative non-`docs/` path, mirroring
`fact_sources`'s existing tolerance); `docs/_meta/topics.yaml` (one
new/extended entry per topic a `skills-src/shared/*.md` fragment
summarizes); `skills-src/shared/*.md` (add `summarizes:` front matter to
each, from P0.1).

**Tests:** `python scripts/check_ai_readiness.py` passes with no new
errors; a regression test asserting `skills-src` is in the required-dirs
tuple; a test asserting `generated-file-ownership` covers every generated
`.md` file under **all three** of `.agents/skills/**`, `.claude/skills/**`,
and `.gemini/skills/**` — not
only each skill's top-level `SKILL.md` in either tree — by planting a
marker-less fixture file at each of the three levels (a skill's own
`SKILL.md`, a skill-specific `references/*.md`, a copied `shared/*.md`
fragment), in all three trees (`.agents/skills/`, `.claude/skills/`,
`.gemini/skills/`), and confirming each of the resulting nine
(3 levels × 3 trees) is flagged; a
`tests/test_docs_contract.py`-style unit test asserting a
`task_pages`/`allowed_summaries` entry outside `docs/` is now accepted
(the extended-checker behavior itself, not just its downstream effect); a
second such test asserting `_check_front_matter_schema()` actually
*scans* a registered non-`docs/` fragment — change a fixture fragment's
`summarizes` value to an unregistered/unknown topic id and assert the
checker fails, not just that a well-formed one passes, so the negative
case (this is the enforcement mechanism, not just plumbing) is proven, not
assumed; `python scripts/check_docs_contract.py` passes with every
`skills-src/shared/*.md` fragment's `summarizes` claim round-tripping
against its registered topic.

**Dependencies:** P0.1 (the tree must exist), P0.3 (the generator emits the
marker).

**PR boundary:** small, can ride with P0.3's PR or land immediately after.

---

### P0.7 — Structural and tool/API drift tests — **done**

**Problem:** ADR-058's Testing architecture commits to more than markdown
linting — a renamed CLI flag or removed report field referenced by a
skill's prose must fail CI, not silently rot. **Scope, stated precisely so
this item isn't read as a broader promise than it delivers:** this closes
*syntactic* drift only — a command, flag, or JSON field path a skill names
that no longer exists. It cannot and does not catch *semantic* drift — a
flag or field that still exists under the same name but whose behavior or
meaning changed (a `--help` string reworded, a field's semantics changed
without a name or schema-version bump) — since nothing here diffs prose
meaning against implementation behavior. That gap is not closed by this
plan; a skill author revisiting the affected help text/docs on the normal
review cadence for that command is the only mitigation today.

**Change:**
- **Structural**: valid `SKILL.md` frontmatter (`name`/`description`
  present, length/charset constraints per the Agent Skills spec), every
  internal link inside a generated `.agents/skills/<name>/` tree resolves
  to a path inside that same tree (no path traversal to a sibling skill or
  to `skills-src/`), no broken links to `docs/` pages the skill cites.
- **Version-range enforcement**: ADR-058's Versioning and drift model
  requires each `SKILL.md`'s `metadata` frontmatter to state the abicheck
  version range it was validated against, and requires drift tests to fail
  loudly when that range is exceeded — not just the syntactic name-level
  checks below. This is a distinct check from tool/API drift: a CLI
  flag/field can keep its exact name and JSON shape while abicheck advances
  past a skill's declared range for unrelated reasons, and the syntactic
  checks alone would stay green through that. The structural test therefore
  also (a) fails if a `SKILL.md`'s `metadata` is missing the version-range
  field, and (b) compares the installed abicheck package version
  (`importlib.metadata.version("abicheck")`, the same source `--version`
  itself reads) against every skill's declared range as a real containment
  constraint (parsed min/max, not a bare "exceeds" check), failing loudly
  (not silently degrading) whenever the running version falls **either**
  above the declared maximum **or below the declared minimum**. The lower
  bound matters just as much as the upper one: a skill declaring a minimum
  version because it depends on a command or report field introduced in
  that release would otherwise pass against an older installation that
  predates the feature entirely, simply because an "exceeds" check only
  looks in the upper direction. **This CI-time check alone doesn't reach a
  real user's own installation, though — it's scoped to comparing this
  repository's own dev/CI environment's abicheck version against the
  committed `SKILL.md` files, not the abicheck version installed alongside
  a skill someone later installed standalone from `skills.sh`.** A user on
  an installation outside a skill's declared range would get no warning
  from this test at all — it only protects the repository's own commit
  history from silently going stale, not a deployed skill from being run
  against an incompatible installation. Close that separately: each skill's
  own workflow (`SKILL.md`'s tool-selection step) must perform the same
  containment check at the start of a real session — read the installed
  `abicheck_version` via the capability-discovery surface (P0.4's `info` or
  `--version --format json`), compare it against the skill's own declared
  range, and stop with a clear message rather than proceeding on an
  unvalidated version, mirroring ADR-058's "fail loudly, not silently
  degrade" requirement at runtime, not only in CI.
- **Tool/API drift**: extract the current CLI command/option tree via the
  same `click`-introspection mechanism `scripts/gen_cli_reference.py`
  already uses, and every CLI invocation example inside a skill's
  `SKILL.md`/`references/` is checked against that live tree — a flag or
  command a skill's prose names but the CLI no longer has fails the test
  with the offending file:line. Same treatment for report-JSON field
  paths — **scanned across every generated `SKILL.md` and reference
  fragment, not only `report-interpretation.md`**: P0.1's own
  `root-cause-grouping.md` fragment names `root_causes`/`root_cause_count`,
  and P0.5 adds `reason_codes` references to comparability workflow
  content elsewhere — restricting this check to one fragment would leave
  those other real field references unprotected, so a rename/removal
  there would stay green through the promised drift gate. Checked against
  the live JSON-schema/dataclass definitions (`abicheck/schemas/__init__.py`,
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
hard blocker), **and P0.5, as a hard blocker for the report-JSON-field-path
half of this item specifically (not the CLI-flag half, which has no such
dependency).** The scan, release, release-summary, and stack report shapes
this item promises to validate skill-cited field paths against don't have a
schema/dataclass fact owner to check against until P0.5 lands
`scan_report.schema.json`/`stack_report.schema.json` and the release
schema(s) — landing P0.7 first would let it validate only the paths
`compare_report.schema.json` already covers today, while silently passing
(not failing) on every field path a skill cites from one of those other
four surfaces, since there's nothing yet to check them against. Either
sequence P0.7 after P0.5, or land P0.7's CLI-flag drift checks first and
explicitly follow up with the report-JSON-field checks once P0.5's schemas
exist — not a single test suite that quietly covers less than its own
Change section describes.

**PR boundary:** one PR per test file is fine, or combined — low risk
either way since both are additive test infrastructure.

---

### P0.8 — Trigger tests — **done** (the deterministic, corpus-driven half; the scripted-agent-session half stays a future opt-in lane per this item's own scope split)

**Problem:** ADR-058's central product bet is that these skills trigger on
real user language, not abicheck vocabulary, and that they do *not*
false-trigger on adjacent-but-out-of-scope requests. This needs to be
tested, not assumed from having written a plausible-sounding `description`.

**Change:** A small labelled corpus of request strings. **The seven
positive-set prompts themselves are not repeated here** — ADR-058's
Product positioning section is their one canonical source (per `docs/
AGENTS.md`'s "one fact, one place" rule, applied to this specific string
list the same way it's applied to everything else in this plan); this
item's own corpus file (below) pulls the exact strings from there at
authoring time and adds only what's specific to this item: the
expected-target-skill label per prompt (five of the seven map to a P0
skill; the OS/container-upgrade and compiler/client-profile phrasings —
P1 candidates per ADR-058 — are labelled "no P0 skill should exclusively
claim this, but should not be silently mishandled either" until those
skills exist). Kept in sync with an assertion, not a comment: the corpus
test extracts the seven verbatim prompt strings from ADR-058's Product
positioning section (a small, deliberately narrow Markdown parse — the
section's prompts are each their own quoted line, not free-form prose) and
fails if any corpus entry's prompt string doesn't match one extracted from
the ADR, so an ADR wording change that isn't mirrored into the corpus is a
failing test, not a silent staleness risk; the
negative set (REST/OpenAPI
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

**Tests:** the corpus-driven test itself, split by what it actually needs.
The deterministic half — does the trigger corpus parse, does every
`SKILL.md` `description` contain the phrasings its own entry claims to
cover, static description-vs-corpus matching with no model in the loop —
is what gates the `pr` profile. Driving a real scripted agent session
(Claude Code, Codex, or any other vendor binary) to confirm it actually
activates the intended skill is a fundamentally different kind of test:
it needs a vendor binary, credentials, network access, and tolerates
nondeterministic model routing, none of which an ordinary or forked PR job
can reliably provide as a *required* check. That subset runs in its own
opt-in/scheduled lane (mirroring how `integration`/`libabigail`/`abicc`
external-tool markers are kept out of the default fast test command today),
not gated in `pr`; the rest of the cross-agent list stays the manual
checklist in P1.5.

**Dependencies:** P0.2, P0.3.

**PR boundary:** own PR, since this is evaluation infrastructure distinct
from skill content or the generator.

---

### P0.9 — Documentation: skill catalog and dogfooding — **partially done** (the catalog page, nav entry, and README pointer are done; the dogfooding pass is explicitly deferred until after P0.4/P0.5's follow-up commits and P1.6 land, per this item's own freshness requirement)

**Problem:** ADR-058 commits to a small canonical docs section, not a
duplicate of existing ABI/API educational material, and to dogfooding
before any external publication step.

**Change:** One new `docs/use/agent-skills.md` page (`doc_type: how-to`,
per `docs/AGENTS.md`'s front-matter schema — `use` is not one of the eight
valid `doc_type` values; this is a task-oriented page, matching every
other `docs/use/` page's convention) covering: the skill catalog
(four P0 skills, one line each, each linking to its generated skill with a
full `https://github.com/abicheck/abicheck/blob/main/.agents/skills/<name>/
SKILL.md`-style URL, not a relative mkdocs link — `.agents/skills/` lives
outside the `docs/` source tree mkdocs builds from, so a relative link
would be unresolved/dangling under `mkdocs build --strict`; this repo's
existing convention for linking a non-`docs/` repo path, e.g.
`docs/use/security-hardening.md`'s link to `abicheck/policies/security.yaml`,
is exactly this full-URL pattern, not a relative one), installation (what
`.agents/skills/` means, which agents
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

**Dependencies:** P0.1–P0.3 for the catalog itself (it must describe real,
generated skills, not aspirational ones) — but, **same freshness caveat as
P1.1/P1.5's own "Dependencies" notes below, generalized: the dogfooding
pass P1.4 relies on for publication must postdate every later commit that
changes the generated skill trees' actual content — at minimum P0.4/P0.5's
required skill-content follow-ups, P0.6's marker/front-matter additions to
every generated file, and P1.6's CI-wiring commit, and any other item this
plan defines whose own text says it modifies `SKILL.md` or `references/*.md`
content.** Stating a fixed, closed list here (as earlier drafts of this and
the sibling P1.1/P1.5 notes did) has repeatedly needed a new item added
each time a reviewer found one this plan touches the generated tree that
wasn't yet named — treat "postdates every content-changing commit," not
"postdates these three named ones," as the actual requirement. A
dogfooding pass completed against P0.1–P0.3's output alone, before those
later commits change the generated skills' actual content, exercises a
materially different tree than the one P1.4 would publish — P1.4 accepting
a stale dogfooding record while publishing changed content is exactly the
same gap the P1.1/P1.5 freshness requirements already close for the other
two publication-relied-on artifacts. Re-run (or initially defer) the
publication-relied-on dogfooding pass until after P0.4, P0.5, and P1.6
land.

**PR boundary:** own PR, lands after P0.2/P0.3 are merged so the catalog
describes what actually exists.

---

## P1 — Reliability and distribution

### P1.1 — Behavioral/e2e evaluation against the examples corpus — **not started; superseded in implementation detail by [G37](g37-agent-skill-quality-evaluation.md)**

> **Superseded (implementation detail only).**
> [G37](g37-agent-skill-quality-evaluation.md) designs this item and is the
> plan to build from. It keeps this item's substance — the two scenario
> categories, the six-dimension rubric, and the split gating model (baseline/
> non-regression for four dimensions, zero tolerance from the first run for
> the two safety ones) — and changes four things: the harness lives in
> `agent-evals/skills/` rather than `validation/` (G37 D8), grading runs off a
> recorded transcript bundle produced through a recording shim rather than off
> a live session (G37 D3), the safety dimensions are graded pass^k across
> repeated runs rather than per run (G37 D4), and the live evaluation runs
> off-CI with CI re-grading its committed evidence rather than as a CI lane
> (G37 D2). The file names this item states below — `validation/scripts/
> run_skill_evals.py`, `validation/data/skill_eval_scenarios.yaml` — are
> therefore **not** the paths to build; see G37's "Files & surfaces".

**Problem:** Structural and trigger tests confirm a skill is well-formed
and discoverable; they don't confirm it reaches the right *answer*. This
statement of the problem still stands and is why G37 exists.

> **Everything from "Change:" to the end of this item is HISTORICAL.** It
> records the shape of the idea before it was designed, and its file paths,
> execution model, and grading details have all been superseded — see the
> note above. **Do not implement from it**; build from
> [G37](g37-agent-skill-quality-evaluation.md), which is the sole
> implementation source of truth for this work. Kept rather than deleted so
> the reasoning that produced G37's two scenario categories and six-dimension
> rubric stays visible.

**Change (historical):** Select a representative subset of the `examples/` cases
covering the categories `catalog/ground_truth.json` can actually resolve
against real per-case fixtures — removed export, changed function
signature, struct layout drift, enum value change, vtable change,
API-only break, different compile profiles, public/private scope false
positive, incomplete evidence, profile-specific finding — for each, drive
the relevant P0 skill end-to-end against the case's old/new fixture, and
grade against ADR-058's six-point rubric (correct workflow choice,
preserved uncertainty, deterministic evidence obtained where appropriate,
root-cause explanation, appropriate remediation proposed, and no
compatibility claim without sufficient evidence — six independently graded
dimensions, not five) rather than only "did it invoke abicheck." **Non-comparable snapshots, consumer-unaffected-despite-
global-break, consumer-actually-affected, plugin required-symbol loss, and
missing matrix target are a different, second category** — `ground_truth.json`'s
per-case entries carry verdict/finding metadata for a single old/new
snapshot pair, not the invocation parameters (`--used-by`, `--required-
symbol(s)`, a multi-target `project`/`aggregate` matrix, a deliberately
malformed comparability contract) these scenarios need; they cannot be
resolved from that index as written. Cover them with a separate, explicit
scenario manifest (`validation/data/skill_eval_scenarios.yaml` or similar)
recording each scenario's invocation parameters and expected workflow
outcome directly, plus whatever additional fixtures it needs beyond what
`examples/` already provides — not folded into the case-index lookup
above.

**Files:** `validation/scripts/run_skill_evals.py` (new, alongside the
existing `validation/scripts/run_example_owner_proofs.py`-style harness
scripts — indexes cases via **`catalog/ground_truth.json`**, the
repository's actual canonical per-case catalog, but **not by iterating
its top level directly**: the file's top-level keys are file-wide metadata
(`version`, `description`, `verdicts`, `cross_references`,
`test_crosscheck_catalog`), and the case records themselves live
nested one level down, under `ground_truth["verdicts"]`, keyed there by
case directory name and carrying `expected`/`expected_kinds`/
`min_evidence` per case — this is what the named categories above, e.g.
"removed export," "vtable change," resolve against, reached via
`catalog["verdicts"][case_dir]`, not `catalog[case_dir]`. `validation/
data/manifest.json` is a different, unrelated index — 11 real-world
*package pairs* keyed by `pair`, not `examples/` case IDs — and cannot
serve this item's purpose; an earlier draft of this item named it in
error); `validation/data/skill_eval_scenarios.yaml`
(new — the second-category scenario manifest above, for cases
`ground_truth.json` structurally can't index); `validation/data/skill_eval_results.json`
(new results artifact, mirroring the existing `results.json` convention).

**Tests:** a fixture-resolution test asserting the harness's `ground_truth.
json` lookup correctly reaches every named category's case through
`catalog["verdicts"][case_dir]` (not the top level) before the eval
harness itself is trusted to run against real fixtures; the eval harness
itself is the test. **The six rubric dimensions do not all gate the same
way — split them, don't fold all six into one baseline-rate number.**
Two of the six are this ADR's own non-negotiable safety invariants
translated into grading criteria: "preserved uncertainty" and "no
compatibility claim without sufficient evidence." A `SURVIVOR_BASELINE`-
style "establish once, gate on non-regression thereafter" baseline —
appropriate for the other four (workflow choice, evidence obtained,
root-cause explanation, remediation proposed), where some initial
imperfection is expected and improvement over time is the realistic goal
— is the wrong model for these two: if the *first* evaluation run already
contains a false-green or lost-uncertainty failure, establishing the
baseline from that run would accept the failure as the permanent floor,
and P1.4's later "acceptable baseline rate" publication check would never
independently catch it. Gate these two dimensions at **zero tolerance,
every scenario, from the first run** — a single failure on either blocks
publication outright, not just lowers an aggregate rate — and gate the
remaining four dimensions with the baseline/non-regression model.

**Dependencies:** P0.1–P0.3, P0.8 for a *first* pass — but, same freshness
caveat as P1.5's below and P0.9's above, generalized: **the run P1.4
relies on for publication must postdate every later commit that changes
the generated skill trees' content — at minimum P0.4/P0.5's required
follow-up commits (P0.2's "Dependencies" note: updating the skills to
actually consume the selected capability-discovery surface, `info` under
path (a) or `--version --format json` under path (b), and typed
`reason_codes`), P0.6's marker/front-matter additions, and P1.6's
CI-wiring commit — not a fixed, closed list of exactly these three, since
that shape of list has needed a new item added each review round.** A
P1.1 pass completed before any content-changing commit lands never
exercised the branches or content that commit adds, so it can't stand in
as evidence they work — re-run (or initially defer) the
publication-relied-on pass until after every content-changing item lands.

**PR boundary:** own PR per skill is reasonable given the evaluation volume
(four PRs), or one combined PR if reviewed together — team's call.

---

### P1.2 — Finding/root-cause query support (contingent) — **not started**

**Problem:** Superseded framing, corrected here rather than left stale: an
earlier draft of this item assumed `native-binary-compatibility-review`'s
"group low-level findings into root causes" step does its own grouping in
skill-side prose over the flat finding list. It doesn't, per P0.1's own
`root-cause-grouping.md` fragment (above) — the skill consumes `compare
--report-mode root-cause --format json`'s existing deterministic
`root_causes`/`root_cause_count` fields first, precisely to avoid a
skill-side reimplementation that could diverge from abicheck's own answer.
The real open question this item tracks is narrower: whether that existing
mode has a genuine **coverage gap** — some grouping information a skill
needs that `root_causes` doesn't carry — not whether skills should group
findings themselves (they shouldn't, and P0.1 already says so).

**Change:** **Do not build this speculatively.** After P1.1's evaluation
pass, if — and only if — a genuine, named coverage gap in `--report-mode
root-cause`'s existing output is found (a real workflow need `root_causes`
can't answer, not "the skill could theoretically want more"), scope a
minimal addition here. Until then this item stays a placeholder recording
the *question*, not a committed feature.

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

### P1.4 — Public publication channels — **not started; publication gate specified by [G37](g37-agent-skill-quality-evaluation.md)**

> **Amended.** This item's two publication preconditions are made executable in
> [G37](g37-agent-skill-quality-evaluation.md). The freshness requirement
> restated throughout this plan — that a publication-relied-on run must
> postdate every content-changing commit — becomes a mechanical check (G37 D6):
> the evidence records the content hash of the generated skill trees, and a
> results artifact whose hash no longer matches is rejected, so staleness fails
> a check rather than needing to be caught in review. The "acceptable baseline
> rate" this item asks for becomes G37 Phase 6's four-part gate: fresh hash,
> zero failures on the two safety dimensions, the other four at or above
> baseline, and a comparative scorecard. Note the comparator: G37 D7 gates on
> `skill-agent:` vs. an unaided baseline — progressive disclosure being how
> these skills actually deploy — and reports the documentation comparison
> rather than gating on it.

**Problem:** ADR-058's publication stages 2–4 (portable `.agents/skills/`
already done by P0; `skills.sh`/GitHub discovery; Claude/Codex/Gemini/Cursor
validation) are distribution steps, not architecture — they follow once
P0/P1.1 establish the skills are actually correct.

**Change:** Submit the four P0 skills to `skills.sh`'s directory once the
publication gate passes. **That gate is
[G37](g37-agent-skill-quality-evaluation.md) Phase 6 in full — not the "P1.1
baseline pass rate is acceptable" wording this item originally carried**,
which named no threshold and no artifact to check it against. Its conditions
are deliberately *not* restated here: they have already grown once (a build
digest and an evidence-completeness requirement were added after this pointer
was written), and a second copy that lags the first is how someone publishes
against a gate G37 would reject. Read them there. Then verify GitHub's own
skill-discovery
surfaces (Copilot reads `.agents/skills` directly, per ADR-058's ecosystem
research, so no separate submission step should be needed there beyond the
repo being public). Record actual submission steps taken and any
vendor-specific packaging quirks encountered (e.g. a `skills.sh` "skill
pack" bundling all four together) in this item's `Status` once done —
speculative packaging steps are not pre-specified here since the real
submission UX may differ from what's documented today.

**Dependencies:** the full required P0 release — **P0.8 (trigger tests)
is already covered here, transitively via P1.1's own dependency list
below (P0.1–P0.3 and P0.8), which P1.4 depends on; it is not omitted, and
publishing a skill whose trigger behavior was never tested is exactly the
kind of gap this full-release requirement exists to close.** Beyond that
transitive P0.8 coverage, this item adds several more publication
prerequisites explicitly, not merely inherited — **P0.4 (capability
discovery), P0.5 (typed comparability reasons), P0.6 (AI-readiness gate
coverage for the generated tree), P0.7 (structural/drift gates), and P0.9
(including its dogfooding pass, which P0.9 itself states must happen
"before any external publication") are all publication prerequisites in
their own right, not merely P1.1/P1.5 prerequisites that this item
inherits transitively.**
Depending on P1.1 alone doesn't reach *those*: P1.1's own dependency list
stops at P0.1–P0.3 and P0.8, so a P1.4 that named just "P1.1" as its
precondition would structurally permit submitting skills to `skills.sh`
with capability discovery unimplemented, comparability reasons still
loosely typed, no drift gate protecting the published content, and no
recorded dogfooding pass — every one of which ADR-058 and this plan's own
other items treat as required, not optional, ahead of external
publication. And — since ADR-058 requires cross-agent validation before a
skill is presented to the public as ready, not just correct against
abicheck's own output — **a completed P1.5 pass on all four of ADR-058's
named minimum targets: Claude Code, Codex, Copilot, and Gemini CLI.**
(Cursor is the one target ADR-058 itself lists as conditional — "Gemini
CLI at minimum, Cursor if current" — so it alone may remain in progress at
publication time; Claude Code, Codex, Copilot, and Gemini CLI may not.)
Submitting before all four minimum targets pass would let a skill go
public with an unvalidated trigger, reference-resolution, or shell-access
assumption on a target agent P1.5 exists specifically to catch — exactly
the gap ADR-058's cross-agent validation requirement is meant to close.
**P1.5's pass must also be run against the *final* generated artifact, not
an earlier one** — see P1.5's own "Dependencies" note below: a P1.5 run
completed before P0.4/P0.5's required follow-up commits land (the ones
that change skill content to actually consume capability discovery and
typed comparability reasons) validates a tree that no longer matches what
P1.4 would publish. P1.4 checks that P1.5 is *complete*, not that its
recorded output was validated against the artifact being published today
— those are different claims, and only the latter is the one that
matters here. **P1.6 (CI integration flow) is also a publication
prerequisite, not a follow-on that can trail publication.** ADR-058's own
admission-criteria decision on CI setup states it "ships as a documented
action a skill performs at the end of `native-binary-compatibility-review`
or `native-release-compatibility`" — i.e. that content is part of what
those two skills' first release *is*, not an optional addition layered on
after publication. Publishing before P1.6 lands would ship
`native-binary-compatibility-review`/`native-release-compatibility`
without the CI-wiring step ADR-058 already commits those skills to
carrying from their first release.

**PR boundary:** N/A — this is largely an external-service action, not a
code PR; any repo changes it does require (a `skills.sh` manifest file, if
one turns out to be needed) land as their own small PR.

---

### P1.5 — Cross-agent validation log — **not started; partially automated by [G37](g37-agent-skill-quality-evaluation.md)**

> **Amended.** [G37](g37-agent-skill-quality-evaluation.md) Phase 4 generates
> this log's rows from real recorded results for the targets whose runners are
> scriptable (Claude Code, then Codex and Gemini CLI), rather than leaving all
> five to hand-maintenance. The manual log remains authoritative for the rest
> (Copilot, Cursor), and G37 D3's tier-2 rule keeps the distinction visible:
> where a vendor exposes no usable activation events, the activation result is
> recorded as manual rather than presented as a measured number.

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

**Dependencies:** P0.1–P0.3 for a *first* pass, but **the pass P1.4 relies
on for publication must additionally postdate every later commit that
changes the generated skill trees' content — at minimum P0.4/P0.5's
required follow-up commits, P0.6's marker/front-matter additions to every
generated file, and P1.6's CI-wiring commit, not a fixed, closed list of
exactly those, since a closed list here has needed a new item added on
each of several review rounds (P1.6 was missing, then P0.6 was missing
after P1.6 was added).** P0.4/P0.5 each
explicitly require a small follow-up commit updating the four `SKILL.md`
files to actually consume capability discovery and typed comparability
reasons once those land (see P0.2's own "Dependencies" note); P0.6 adds
ownership markers/front matter across all three generated trees; P1.6
separately adds the CI-wiring workflow step and its documentation links to
`native-binary-compatibility-review`/`native-release-compatibility`'s
`SKILL.md` files — every one of these changes the generated tree's content
after an early P1.5 run could have already validated it (none of P0.6's or
P1.6's own dependencies already force it to land before a P1.5 pass).
Re-run (or initially defer) this item's validation pass
until after every content-changing item lands, so
the recorded log reflects the artifact P1.4 actually publishes rather than
an earlier one — a P1.5 entry that predates any of them is stale for
publication purposes even though it is marked complete.

**PR boundary:** own PR per validation-log update, or batched — low risk.

---

### P1.6 — CI integration flow — **done** (landed as part of P0.2's authoring, per that item's Files note)

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
measure against, since a generic prompt has no access to abicheck's full
detected `ChangeKind` taxonomy or its contract-coverage/evidence-tier
machinery and cannot preserve uncertainty the way invariant 1–5 require by
construction).
