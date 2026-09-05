# Case 144: Private Header Leak (Single-Release Audit)

**Category:** Quality (Audit) | **Verdict:** 🟢 COMPATIBLE (bad practice)

## Verdict and consumer impact

Single-release audit: one build's evidence checked against itself, no
baseline. abicheck's verdict is `COMPATIBLE` — the ABI hasn't broken — but
the audit flags an advisory finding: the public function `make_widget()`
returns `detail::WidgetImpl*`, and `detail::WidgetImpl` is a type defined
only in a **private, non-installed header**. Consumers linking against the
public headers cannot legally name `detail::WidgetImpl` (they'd have to
reach into a header the library never installs), yet the pointer type is
already part of the exported signature. The maintainer never reasoned about
this as public API surface, but it already is one — the day the private
header's layout changes, every caller holding a `detail::WidgetImpl*` is
silently exposed to a layout mismatch with no compiler warning to catch it.

## What this snapshot contains

`snapshot.abi.json` is a single, hand-built `AbiSnapshot` for one build of
`libdemo.so`, carrying both the exported function's signature and the
provenance of the type it returns:

| Source in the snapshot | What it records |
|---|---|
| Binary export table (L0, `elf.symbols`) | `_Z11make_widgetv` (`make_widget`) exported with default visibility |
| Public-header AST (L2, `functions[].return_type` / `types[].origin`) | `make_widget` returns `detail::WidgetImpl *`; the `detail::WidgetImpl` struct entry carries `origin: private_header` |

## abicheck command

```bash
abicheck scan snapshot.abi.json
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

crosscheck:private_header_leak present   public API ↔ private-header provenance:
  1 public declaration(s) exposing one of 1 private type(s)

ABI-hygiene catalog (intra-version, advisory)
  [warning] private_header_leak: 1
```

## Minimum evidence

`min_evidence: L2` — the export table alone (L0) sees only that
`make_widget` is exported; it carries no type information at all. The
public-header AST (L2) is what supplies both the return type and that
type's provenance (public vs. private header), which is the fact the
cross-check needs.

## Why abicheck catches it

`private_header_leak` is a **cross-source check**: abicheck walks the public
API surface reachable from the headers it was pointed at, resolves every
type referenced in a public signature, and checks that type's own
provenance. A public declaration (`public_header_ast`) that resolves to a
type whose provenance is `private_header` is exactly this finding — a single
source can't produce it, since the export table has no type information and
a plain header parse of the public header alone wouldn't know the returned
type actually lives in a *different*, non-installed header.

## Why this matters for a real release

`detail::WidgetImpl` is undocumented, so the maintainer can freely resize or
reorder its fields believing nothing external depends on the layout. But
consumers holding a `detail::WidgetImpl*` from `make_widget()` — even if
they never dereference it and only pass it back into the library — still
depend on `sizeof`/alignment assumptions baked in wherever the pointer is
stored or copied. Catching the leak now, before a consumer builds tooling
around the returned pointer, is cheaper than treating a later private-header
change as a compatibility incident.

## Safe redesign

Either install the header defining `detail::WidgetImpl` (promoting it to
real public API, with the compatibility obligations that implies), or hide
it behind an opaque handle — return `void*` or a forward-declared incomplete
type, and only operate on it through library-provided functions (PIMPL).

## Cross-tool comparison

`private_header_leak` is a cross-source check unique to abicheck's audit
mode — it reconciles a public function's signature against the provenance of
the type it references within the *same* build, which isn't something
`abidiff`/`abi-compliance-checker` do (they diff two ABI dumps against each
other, not a binary's public surface against its own header provenance).
