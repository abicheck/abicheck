# Case 120: Internal Struct Fields Reordered (Non-Public, Scoped)

**Category:** Public-Surface Scoping (ADR-024) | **Verdict:** ✅ NO_CHANGE

## Verdict and consumer impact

`struct InternalStats`'s fields are swapped (`calls`/`total` reordered)
between v1 and v2, which normally changes field offsets — an ABI break for
that struct. But `InternalStats` is declared in the header only for other
translation units *inside* the library; it's never referenced by any
function, variable, or cast anywhere in the compiled source, and no exported
function reaches it. The public API (`Point`, `translate()`) is unchanged,
so no existing binary that uses only the public surface is affected.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `struct InternalStats { int calls; long total; };` | `struct InternalStats { long total; int calls; };` |
| `Point translate(Point p, int dx, int dy);` *(unchanged)* | `Point translate(Point p, int dx, int dy);` *(unchanged)* |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so \
  --header old=v1.h --header new=v2.h \
  --ast-frontend clang --compiler "$(command -v clang)" \
  --scope-public-headers --show-filtered
```

## Expected abicheck finding

```text
Verdict: NO_CHANGE (exit 0)

_No ABI changes detected._
```

Unlike case118/case119, this real run produces **no** filtered-ledger entry
either (`--show-filtered` shows nothing to filter). Two things compound
here: `InternalStats` is never instantiated or referenced anywhere in
`v1.c`/`v2.c`, so it emits no DWARF debug info at all (DWARF only describes
types the compiler actually used); and the Clang AST frontend used for
header evidence in this environment (`--ast-frontend clang`, since castxml
isn't installed here) records each field's name and type but not its
computed byte offset. With no concrete offset evidence on either side,
comparing the two field lists by name finds no difference to report or
filter — the verdict lands on `NO_CHANGE` directly rather than via the
scoping filter. A castxml-backed run, which does compute header-derived
field offsets, would be expected to surface the reorder as a
filtered-non-public finding the way case118/case119 do — that path isn't
verified in this sandbox.

## Minimum evidence

`min_evidence: L2` — telling `InternalStats` apart from a genuinely public
type requires the public header AST, the same as case118/case119. In
practice, *detecting the reorder itself* (as opposed to just scoping it)
would additionally need offset-computing header evidence (e.g. castxml) or
DWARF from a build where the struct is actually instantiated — neither is
available in this environment, so this run demonstrates the scoping
decision resolving to NO_CHANGE, not the underlying offset-diff mechanism.

## Why abicheck catches it (and doesn't report it)

With `-H`/`--header`, abicheck resolves the public surface — exported
symbols plus their reachable type closure (ADR-024) — and would evaluate any
detected `InternalStats` layout difference against that closure. Because
`InternalStats` is never reached from `translate()` or any other exported
declaration, any such difference is routed to the filtered/audit ledger, not
the reported findings, keeping the verdict `NO_CHANGE` regardless of whether
the underlying offset evidence is available.

## Runtime failure demonstration

No observable effect on existing binaries — this is the intended, compatible
outcome. Building `app.c` against v1 and swapping in the v2 `.so` without
recompiling produces identical output both times:

```bash
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → translate -> (11, 22)

gcc -shared -fPIC -g v2.c -o libfoo.so   # swap, no recompile
./app
# → translate -> (11, 22)   (unchanged)
```

## Safe redesign

N/A — this is the pattern to follow, not to avoid. Keeping internal
bookkeeping types out of the reachable closure of the public API lets their
field order (and everything else about their layout) evolve freely, as long
as they truly stay unreachable from any exported declaration.

## Cross-tool comparison

`abidiff`/ABICC have no equivalent of ADR-024 public-surface scoping — both
diff every type present in their evidence regardless of reachability from an
exported declaration. `abidw`/`abidiff` are not installed in this
environment, so no cross-tool output is reproduced here.
