# Case 184: Internal Enum Churn, Scoped Out by Private-Header Origin

**Category:** Public-surface scoping (ADR-024) | **Verdict:** ✅ NO_CHANGE

## Verdict and consumer impact

`InternalMode::MODE_B` changes value from `1` to `9`. `InternalMode` is
declared in `v1_internal.h`/`v2_internal.h` — a private implementation-detail
header, transitively `#include`-d by the public umbrella header (`v1.h`) but
never itself part of the installed public header set. No public function
signature, field, or typedef reaches `InternalMode` — `translate()` only
uses it as a local variable inside its own function body. The public API
(`Point`, `translate`) is unchanged, and consumers see no behavioral
difference at all: this is the intended, compatible outcome for a value
change confined to an unreachable, private-header enum.

## Old/new diff

| v1_internal.h | v2_internal.h |
|----------------|----------------|
| `typedef enum { MODE_A = 0, MODE_B = 1 } InternalMode;` | `typedef enum { MODE_A = 0, MODE_B = 9 } InternalMode;` |

`v1.h`/`v2.h` (the public umbrella header) and `translate()`'s own signature
are byte-identical between versions.

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so \
    --header old=v1.h --header new=v2.h --ast-frontend clang --show-filtered
```

## Expected abicheck finding

```text
Verdict: NO_CHANGE (exit 0)

Filtered as non-public ABI surface (1 finding, --scope-public-headers):
  - enum_member_value_changed: InternalMode::MODE_B (private-header)
```

## Minimum evidence

`min_evidence: L2` — telling `InternalMode` apart from a genuinely
public-header enum requires header/AST evidence: the header-AST provenance
classifier has to see which file (`v1_internal.h`, not `v1.h`) actually
declared the enum. castxml is abicheck's documented default AST backend for
this evidence level; clang is a supported alternative frontend
(`--ast-frontend clang`) used for the run above.

## Why abicheck catches it

An enum constant is consumer-visible the instant its header is included —
unlike a struct's opaque layout, it behaves like a `#define`. So
`abicheck/surface.py` deliberately seeds *every* header-declared enum into
the public surface regardless of reachability (ADR-024) — reachability
alone is not trusted to clear an enum the way it clears an unreferenced
struct (case118). That override is skipped only when the enum's own
declaration origin is confidently non-public: because `v1_internal.h` was
never itself passed via `-H`/`--header`, the header-AST provenance
classifier tags `InternalMode` as `PRIVATE_HEADER` rather than
`PUBLIC_HEADER`, letting ordinary reachability filtering apply after all —
and since nothing public reaches it, the value change is filtered.

Without `--scope-public-headers`, or if `InternalMode` were declared
directly in the public header itself (no separate private header — a
confident `PUBLIC_HEADER` origin), the same value change is reported as
`BREAKING` (`enum_member_value_changed`) instead — the ADR-024 override
keeps genuinely public-header enums on the surface even when unreferenced
by any function signature. See `case08_enum_value_change` and `case19` for
that non-scoped, publicly-declared-enum baseline.

## Runtime failure demonstration

**Severity: none — no observable effect on existing binaries.**

```bash
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → translate -> (11, 22)

gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → translate -> (11, 22)   (identical output)
```

Both builds produce the same result: `InternalMode`'s value change never
crosses into `translate()`'s observable behavior, confirming the NO_CHANGE
verdict is genuinely safe, not merely unproven.

## Safe redesign

N/A — this is the intended, compatible outcome for a value change confined
to a private-header enum with no public reachability. If `InternalMode`
were declared in the public header, or if any public function took or
returned it, abicheck would report the change.

## Cross-tool comparison

`abidiff`/ABICC have no concept of header-provenance-based public-surface
scoping (ADR-024) — they either see every DWARF-visible enum member as ABI
surface or none, with no notion of "declared in a private header,
transitively included." This class of finding is abicheck-specific, so no
cross-tool reproduction is included here.

## References

- Related cases:
  [case118_internal_struct_field_added_scoped](../case118_internal_struct_field_added_scoped/README.md),
  [case08_enum_value_change](../case08_enum_value_change/README.md)
