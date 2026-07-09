# ADR-039: Build-Context Reconciliation of Context-Free Header-Parse Artifacts

**Status:** Accepted — implemented (diff-layer reconciler + collection-layer producer)

## Context

abicheck parses public headers **context-free** by default: the header AST is
captured with no compile database, so no build-time `-D` defines are applied
(see `abicheck/buildsource/crosscheck.py`, which notes "the header AST was
captured context-free"). That is deliberate — it keeps the header layer usable
without a build — but it has a sharp failure mode.

When a public struct field lives inside a preprocessor guard:

```c
// v1 public header                     // v2 public header (b becomes opt-out)
struct S { int a; int b; };             struct S { int a;
                                        #ifdef KEEP_B     // project ships -DKEEP_B
                                            int b;
                                        #endif
                                        };
```

…and both releases are actually built **with** `-DKEEP_B`, the shipped ABI is
identical (`{a, b}` in both). But a context-free parse evaluates the v2 header
with `KEEP_B` undefined, so it sees `{a}` — and the comparison raises a
`type_size_changed` / `type_field_removed` **false positive** for a field whose
real presence never changed.

This is exactly the class the per-depth false-positive analysis
(`validation/false-positive-depth-analysis-2026-07.md`) identified as *only*
clearable with evidence above the header layer: the `binary` depth is blind
(both stripped binaries are identical), `headers` false-positives, and only
`build` context (the real `-D` set) resolves it. It is the `header_build_context_mismatch`
(L3) situation, but here we want to *clear the finding*, not just flag the drift.

## Decision

Add an **opt-in, diff-layer reconciliation pass** — `abicheck/diff_reconcile.py`,
enabled by `compare(..., reconcile_build_context=True)` and the CLI
`--reconcile-build-context` flag — that moves such context-free header-parse
artifacts out of the verdict into an audit bucket (`DiffResult.reconciled_changes`),
using two new pieces of build-context evidence carried on the snapshot:

1. **`AbiSnapshot.conditional_fields: dict[str, dict[str, dict[str, str | bool | int | None]]]`** — a
   `{type: {field: {guard, type, is_bitfield, bitfield_bits, access, is_const, is_volatile, is_mutable}}}`
   registry of the fields a header parse knows are guarded by a `#if defined(GUARD)` region,
   **whether or not** the context-free parse pruned them from the type's `fields`
   list. It carries each field's **full declaration**, not just its guard,
   precisely because the artifact we clear is a *pruned* field — the context-free
   `fields` list no longer contains it, so its declaration must live here for a
   type change on it to remain visible.
2. **`AbiSnapshot.build_context_defines: set[str]`** — the macros the build
   actually defines (harvested from the compile database).

A guarded field's *real presence* on a side is: it is present unless guarded on
**that side** by a macro that side does not define, plus any registry-only
(pruned) field whose guard that side *does* define. Only **field-presence**
findings — `type_field_added`, `type_field_added_compatible`,
`type_field_removed` — are reconcilable, and one is reconciled **iff the two
sides' real field-name sets are equal** (each computed from that side's own
registry and defines).

### Soundness — do not clear what the defines do not explain (Codex review #498)

Two guards make this sound:

- **Presence only, never size/offset (P1-b).** Build defines prove field
  *presence*, not record *size* / *offset* / *alignment*. So
  `type_size_changed` and `type_field_offset_changed` are **never** reconciled.
  A correctly build-aware snapshot carries the artifact-accurate size, so a pure
  context-free-pruning artifact surfaces as a `type_field_removed` with **no**
  size delta; a real size change co-located with a pruned guarded field keeps its
  `type_size_changed` and the verdict stays breaking.
- **Per-side guards (P1-a).** Each side is evaluated with its own registry and
  defines — a guard the *new* header adds is never applied to the *old* build's
  field. So a field unguarded in old but guarded-and-undefined in new is a real
  removal (the sides' real sets differ) and is kept.

### Authority rule (ADR-028 D3) is preserved

This never deletes an artifact-proven break: an **unconditional** add/remove, or
a guard that resolves **differently** between the two builds (a genuine
flag-driven ABI change), changes the real field set and is kept. With no build
evidence (empty defines / empty registry) the pass is a **no-op** — the
context-free false positive survives exactly as the un-reconciled tool reports
it. Reconciled findings are recorded (`surface_exclusion_reason=
"build-context-reconciled"`, `evidence_category="build_context"`) and disclosed
under `--show-filtered`, in the JSON report (`build_context_reconciled`), and in
SARIF (`buildContextReconciled`) — never silently dropped.

## Producing the conditional-field registry (collection layer)

The collection layer — `abicheck/header_conditionals.py` — populates both pieces
of evidence during a `dump` when a compile database is supplied
(`cli_dump_helpers`, next to where `parsed_with_build_context` is set):

* `defines_from_compile_db` / `defines_from_flags` harvest the build's active
  `-D` set into `build_context_defines`;
* `scan_conditional_fields` scans the public-header **source** for record fields
  wrapped in a *single positive* `#ifdef GUARD` / `#if defined(GUARD)` region —
  including the ones a context-free castxml parse pruned — recording each field's
  guard and declaration into `conditional_fields`.

It is a **best-effort, conservative** scanner, not a full C preprocessor: it
records a field only when the pattern is unambiguous (a single positive guard, a
plain member declaration directly inside a `struct`/`class`/`union` body).
Negative (`#ifndef`), compound (`&&`/`||`/`!`), and nested guards are
deliberately **not** recorded — a missed field just means no reconciliation
(safe), and the reconciler's own per-side declaration check catches any mis-scan
before it could clear a real change. A plain context-free dump with no compile
database leaves both fields empty, so the reconciler stays a safe no-op there.
The example case `examples/case164_preproc_conditional_field/` ships a committed
fixture pair so the diff-layer capability is also exercised compiler-free in the
fast lane; `tests/test_header_conditionals.py` covers the scanner end-to-end.

## Consequences

- **New capability, off by default.** No existing verdict changes unless a
  caller opts in *and* the snapshots carry the new evidence.
- **Schema.** Both fields are additive and optional; a v8 reader ignores them,
  and a snapshot without them loads with the safe defaults (`guard=None`, empty
  define set), so no schema-version bump is required.
- **Complements, not replaces, in-context parsing.** The fully-correct fix is to
  parse headers with the build's defines so the phantom never arises; this pass
  is the diff-layer safety net for snapshots that were captured context-free but
  carry enough build context to prove the non-change.

## Alternatives considered

- **Suppress purely on artifact size authority (DWARF/symbol size unchanged).**
  Rejected as the primary mechanism: it needs both a context-free header
  field-list *and* an authoritative artifact size retained for the same type,
  which the merged snapshot does not keep distinctly, and it cannot explain
  *which* field the delta belongs to.
- **Do nothing at the diff layer; only fix collection (in-context parsing).**
  Correct long-term, but leaves every already-captured context-free snapshot
  false-positive with no recourse; the opt-in reconciler is the bridge.
