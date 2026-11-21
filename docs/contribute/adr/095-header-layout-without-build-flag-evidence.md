# ADR-095: A Header-Derived Layout Without Build-Flag Evidence Cannot Back a Padding-Dependent Compatible Verdict

**Date:** 2026-11-21
**Status:** Accepted (2026-11-21) — implemented. New risk kind `LAYOUT_BUILD_CONFIG_UNVERIFIED` (`compare/layout_build_config.py`), abiforge expectation rule `flag_invisible_layout` (`abiforge/model/expect.py`). Plan [abicheck-abiforge-completeness](../plans/abicheck-abiforge-completeness.md) §11.2, WS-2 A3.

Governed by: [ADR-028](028-source-build-evidence-pack.md) / [ADR-063](063-one-semantic-pipeline.md) (weaker evidence narrows conclusions), [ADR-064](064-canonical-gate-algorithm-and-exit-decision.md) (exit decision), [ADR-090](090-unresolved-surface-is-not-a-clean-verdict.md) (a clean claim needs type-level evidence), [ADR-093](093-declared-change-kind-relations-and-observability.md) (`observable_at`).

## Context

The primary Linux configuration is a stripped `.so` plus public headers (`{B,H}`, L2). Its record and enum layout comes from parsing the headers under the *default* layout configuration of the target. Some compile switches change layout without anything in the header saying so: `-fpack-struct=N`, `-fshort-enums`, `-mms-bitfields`, `-malign-double`. A stripped binary records none of them (no DWARF, no `DW_AT_producer`, no build data).

The stripped nightly lane (plan §11.2, WS-2 A3) showed the consequence. Built with `-fpack-struct=1`, `struct FC { int a; char b; }` grows to `{ int a; char b; char c; }`: the binary's `sizeof` goes 5 -> 6, a real break, but the header layout keeps 3 bytes of tail padding, so `c` reads as `TYPE_FIELD_ADDED_COMPATIBLE`, verdict `COMPATIBLE`, exit 0. The same holds for a field inserted into an interior hole, and for an enumerator that pushes a `-fshort-enums` enum from 1 to 2 bytes. Those are false-clean answers. `LAYOUT_UNVERIFIABLE` does not cover them: it fires only when layout evidence exists on one side.

## Decision

**D1. The class is "a compatible layout conclusion that holds only because of padding or enum width".** Most `{B,H}` layout conclusions do not depend on the unseen flags: a size change, an offset change, a removed field or a changed type reads as a break under any of them. Only two conclusions are *made true by* the default layout and false under a layout flag:

- a record (not a union) gained a named field and its header size is unchanged, so the field sits in padding that `-fpack-struct` removes;
- an enum with no fixed underlying type (not scoped, `int`/`unsigned int` underlying) has a value range whose `-fshort-enums` width (1, 2 or 4 bytes) changed while its header size is unchanged.

**D2. Trigger only on missing build-flag evidence, on both sides.** The signal fires when both snapshots read their layout from headers (`from_headers`), neither carries DWARF or other debug layout evidence (`debug_info_evidence(...).any`), neither was parsed with a build context (`parsed_with_build_context`, compile database), and neither header parse received a layout switch (`ast_compile_args` names none of `LAYOUT_FLAG_PREFIXES`). If any of these holds, the layout is not merely assumed and the existing detectors decide.

**D3. The signal is a finding, `LAYOUT_BUILD_CONFIG_UNVERIFIED`, with default verdict `COMPATIBLE_WITH_RISK`.** It names the record or enum and the reason. The pair cannot read as a clean `COMPATIBLE`. It is a `RISK_KINDS` member, so the exit code stays 0 under the default gate and it appears in every report format, as every risk finding does. Users who need a hard gate already have `--severity-risk error` / policy reclassification.

**D4. The ground truth follows the evidence.** In abiforge, a stripped `{B,H}` cell without build data or sources whose edit is benign under the default layout but a break under a layout flag owes, at the flagged build, the reduced-assurance kind (or the break itself, where the header parse did see the flag) and at least `COMPATIBLE_WITH_RISK`, judged `evidence-limited`, not `agree`. The same edit's unflagged builds may read `COMPATIBLE_WITH_RISK` (the ceiling rises one step, never to a break).

## Alternatives considered

- **Degrade every `{B,H}` layout conclusion** (ADR-090 style, exit 1). Rejected: every ordinary L2 run would lose its clean verdict, for edits whose answer the flags cannot change.
- **Binary-side corroboration only** (`st_size` of exported objects against the header `sizeof`). Sound when it fires, but it needs an exported object of the affected type; the nightly's false-clean cells have none (`int fc_use(FC*)`). Kept as a future strengthening: a disagreement there is direct evidence and could escalate to a break.
- **A user-declared flag** (`--layout-flag`). Already expressible: passing `-fpack-struct=1` through the header compile options makes the header layout correct, and D2 then silences this signal.
- **`UNCONFIRMED` / exit 1.** Rejected for now: `COMPATIBLE_WITH_RISK` already stops a clean claim and keeps exit 0 for a library that is in fact built with default flags, which is the common case.

## Consequences

- A stripped `{B,H}` compare that adds a field into padding, or widens an unfixed enum's short-enum range, now reads `COMPATIBLE_WITH_RISK` with a finding naming the assumption, instead of `COMPATIBLE`. Exit code unchanged (0).
- The `GAP_FLAG_INVISIBLE_LAYOUT` false-clean entries (`field_added_in_tail_padding`, `field_inserted_in_padding`, `enum_append_wide_value`) leave `abiforge/known.py`.
- Not covered: the wrong-kind cells (`field_removed_without_shrink`, `array_grown_into_padding`, already `BREAKING`) and the `-fshort-enums` false positive (`enum_underlying_narrow`). Those are wrong details or an over-strong verdict, not false-clean, and stay in `known.py` and `known-gaps.md`.
- An edit into padding of a record that is never built with a layout flag now carries one risk finding. That is the trade-off: the header alone cannot tell the two builds apart.
