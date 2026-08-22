---
doc_type: contributor
level: advanced
lifecycle: active
---

# Per-library header roots for `compare_product_directories`

**Origin:** User request (multilib product-comparison work, PR #829) to
substantively address "header handling in the multilib case" — flagged as
explicitly *not* solved by that PR's own header-roots support (a single
flat list applied identically to every library in the product).
**Type:** Initiative plan (single-module slice; touches
`abicheck/product_baseline.py` and its test file only — no CLI, no schema
version bump).
**Effort:** S · **Risk:** low — purely additive, one new accepted input
shape layered on an existing keyword-only parameter; every existing caller
(a bare `Sequence[str]`, or the already-shipped `old_header_roots`/
`new_header_roots` override pair) is unaffected byte-for-byte.
**Status:** Implemented (PR #829) — `HeaderRootsSpec`/`_roots_for_library()`
landed in `abicheck/product_baseline.py`; the "Out of scope" items below
remain open, unattempted follow-ups.

## Problem

`compare_product_directories(old_dir, new_dir, *, header_roots=(),
old_header_roots=None, new_header_roots=None, ...)` resolves headers
**per side**, not **per library**: every matched library pair in the
product is handed the identical `old_headers`/`new_headers` list. That
already closed one real gap (a product relocating its headers wholesale
between releases — `old_header_roots`/`new_header_roots`, PR #829 round
12) but leaves three concrete, still-open failure modes for a product
whose libraries don't all share one header space:

1. **Wrong-library headers reach a compare.** If `liba.so`'s public
   headers live under `include/liba/` and `libb.so`'s live under
   `include/libb/`, passing both roots to every library means `liba.so`'s
   header-AST parse also sees `libb.so`'s headers (and vice versa) —
   harmless if the two header trees never collide, but a real risk when
   they declare same-named types with different definitions (an ODR
   violation across two independently-versioned libraries in one
   product — not hypothetical for a product like oneDAL, where sibling
   libraries can each vendor their own copy of a shared internal header
   during a migration).
2. **No way to scope a plugin/vendored subtree to one library.** A
   product with N libraries and only some of them publishing headers
   (the rest are pure implementation DSOs) has no way to say "these
   roots apply only to `libfoo.so`" — today it's all-or-nothing across
   the whole product.
3. **Redundant, potentially-inconsistent parsing.** (Documented as a
   known gap already, not addressed by this plan — see "Out of scope"
   below — but recording it here since it's the sibling problem this
   slice is adjacent to.) N libraries sharing one *actual* common header
   still get N independent header-AST parses today, each a full
   `run_compare` invocation; this plan does not add a shared cache.

## Goal & acceptance criteria

- `compare_product_directories()` accepts a **per-library** header-roots
  mapping in addition to the existing flat list, so a caller can say
  "these roots apply only to this one library" when the product's
  libraries don't share one header space.
- Fully backward compatible: every existing call — a bare
  `Sequence[str]` for `header_roots`, and/or `old_header_roots`/
  `new_header_roots` overrides — resolves identically to before. No
  existing test in `tests/test_product_baseline.py` changes its
  expected behavior.
- The per-library keying uses the *same* library identity
  `_discover_library_map()` already produces (a path relative to the
  side's own root, e.g. `"lib/liba.so"`) — not a second, parallel
  identity scheme a caller would have to derive separately.
- A library with no entry in a per-library mapping gets an empty header
  list for that side (not a silent fallback to some other library's
  roots) — matching this format's existing "a missing/empty root is
  tolerated, not an error" convention (a library that ships no public
  headers is a normal case, not a misconfiguration).
- `ProductBaselineManifest`'s own `header_roots` field and on-disk schema
  (`abicheck.product-baseline/v1`) are **unchanged** — this plan only
  widens what a caller of `compare_product_directories()` can pass in
  Python, not what `pack_product_baseline()` records on disk. See "Out
  of scope" for why a manifest-level per-library encoding is a separate,
  larger decision.

## Design

New type alias in `product_baseline.py`:

```python
HeaderRootsSpec = Sequence[str] | Mapping[str, Sequence[str]]
```

`header_roots`, `old_header_roots`, `new_header_roots` all widen from
`Sequence[str]` / `Sequence[str] | None` to `HeaderRootsSpec` /
`HeaderRootsSpec | None`. Resolution becomes per-library:

```python
def _roots_for_library(spec: HeaderRootsSpec, library_key: str) -> Sequence[str]:
    if isinstance(spec, Mapping):
        return spec.get(library_key, ())
    return spec  # flat list -- every library gets the same roots
```

`_resolved_headers()` (already a per-side closure in the function today)
becomes per-*library-and-side*, called once per matched pair inside the
existing comparison loop instead of once per side up front — the loop
already iterates `(old_path, new_path)` pairs keyed by the discovery
identity, so the per-library roots lookup slots in naturally using that
same key. A canonical-fallback pair (the round-13 SONAME-bump matching)
uses the *old*-side key for the old roots lookup and the *new*-side key
for the new roots lookup — each side's own discovered identity, not a
shared canonical one, so a per-library mapping keyed by real discovered
paths (the only identity a caller can actually observe) still resolves
correctly even when the two sides' filenames differ.

A `dict`/`Mapping` is distinguished from a `Sequence[str]` via
`isinstance(spec, Mapping)` — a `Sequence[str]` (list/tuple of path
strings) and a `Mapping[str, Sequence[str]]` never structurally collide
(a `str` is technically a `Sequence[str]` of characters, which is why
`header_roots="include"` — a plausible typo — still needs its existing
guard; unaffected by this change since `Mapping` and `str` are already
mutually exclusive isinstance checks).

## Files & surfaces

- `abicheck/product_baseline.py`: `HeaderRootsSpec` type alias,
  `_roots_for_library()` helper, `compare_product_directories()`'s
  three header-roots parameters widened, the per-library resolution
  loop.
- `tests/test_product_baseline.py`: new cases in (or alongside)
  `TestCompareProductDirectoriesHeaderRoots` — a per-library mapping
  scoping roots to one library and not its sibling; a library absent
  from the mapping getting no headers; a mapping combined with the
  round-13 canonical (SONAME-bump) fallback pairing, keyed by each
  side's own discovered identity; the existing flat-list tests
  unchanged (regression guard that the common case didn't move).

## Tests

Direct, `monkeypatch`-based tests following the pattern already
established in `TestCompareProductDirectoriesHeaderRoots`/
`TestCompareProductDirectoriesCanonicalFallbackAndPolicy` (fake
`run_compare`/`_discover_library_map`, no real ELF/compiler needed) —
this is pure Python resolution logic, not a header-AST-parsing change,
so no `integration` marker is needed for the new cases.

## Effort & risk

**S** — one new type alias, one small helper, moving an existing
per-side closure inside an existing loop. **Low risk**: strictly
additive to a function that has no CLI surface yet (library-only, no
`compare` CLI wiring exists for `compare_product_directories()` at
all), so there is no flag-parity/back-compat surface beyond this
module's own Python callers, and the fallback (`Sequence[str]`) path is
unit-tested to be byte-for-byte unchanged.

## Out of scope

- **A shared header-AST cache across libraries sharing one actual
  common root** (failure mode 3 above). This plan only decides *which*
  roots apply to *which* library; it doesn't change how many times a
  shared header gets parsed. Closing that needs a product-wide
  compile-context/cache object threaded through the per-library
  `run_compare` loop — a materially different, larger change (see the
  original chat analysis this plan grew out of, "Вариант B" for the
  header-handling question) that risks the exact per-library
  `-D`/compile-flag isolation bugs this codebase's own AGENTS.md
  "Known gaps" section already documents at length for the unrelated
  L3→L2 build-context fold. Not attempted here.
- **A product-wide cross-library type graph** (real cross-DSO *type*
  ABI breaks — a struct defined in one library's header and consumed
  by value in another's public API). Per-library header roots make it
  *possible* to parse the right headers for the right library, but they
  don't give the diff engine a way to correlate a type change in one
  library's snapshot with its use in a sibling's — that needs a new
  identity layer this codebase does not have today (see "Вариант C" in
  the same prior analysis). Separate, larger project.
- **Manifest-level (on-disk) per-library header roots.**
  `ProductBaselineManifest.header_roots` stays a flat
  `tuple[str, ...]`, unchanged. Making `pack_product_baseline()` itself
  record which header root belongs to which library would need either
  a new manifest field (a real schema addition, `abicheck.
  product-baseline/v2` territory — the on-disk format has an explicit,
  tested MAJOR-rejection contract, so this is not a casual field
  add) or an inferred association (e.g. "a header root under the same
  parent directory as a library" — heuristic, and wrong for a product
  whose public headers live in one shared top-level `include/` instead
  of per-library subtrees, which is at least as common a real-world
  layout). A caller can already express per-library roots *at compare
  time* via this plan's `HeaderRootsSpec` regardless of what the
  manifest recorded, so this is not a blocking dependency — just a
  separate decision about whether the *archive itself* should also
  remember the association, deferred until a real caller needs it.
