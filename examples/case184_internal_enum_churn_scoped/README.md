# Case 184: Internal enum churn, scoped out by private-header origin

**Category:** Public-surface scoping (ADR-024) | **Verdict:** ✅ NO_CHANGE (exit 0, with `--scope-public-headers`)

## What changes

`InternalMode::MODE_B` changes value from `1` to `9`. `InternalMode` is
declared in `v1_internal.h`/`v2_internal.h` — a private implementation-detail
header, transitively `#include`-d by the public umbrella header (`v1.h`) but
never itself part of the installed public header set. No public function
signature, field, or typedef reaches `InternalMode` — `translate()` only
uses it as a local variable inside its own function body. The public API
(`Point`, `translate`) is unchanged.

## Why this needs a different scoping rule than a struct

[case118](../case118_internal_struct_field_added_scoped/README.md) filters an
internal *struct*'s layout change purely by **reachability**: nothing public
names `InternalStats`, so its layout is unobservable to any caller.

Enums don't get that same free ride. `abicheck/surface.py`
(`compute_public_surface()`) deliberately seeds *every* header-declared enum
into the public surface **regardless of reachability** — an enum constant is
consumer-visible the instant its header is included (`ERROR` behaves like a
`#define`, not like a struct's opaque layout), so an internal-looking-but-
technically-header-declared enum must not be silently cleared by reachability
alone (ADR-024; the case20 regression this guards against).

The override is skipped only when the enum's **own declaration origin** is
confidently non-public: `v1_internal.h` is a real header, transitively
included, but it was never passed via `-H`/`--header` — so
`dumper_castxml`/`dumper_clang`'s provenance classifier tags `InternalMode` as
`PRIVATE_HEADER`, not `PUBLIC_HEADER`, and reachability applies normally after
all. Since nothing public reaches it, it's filtered.

```bash
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h \
    --show-filtered
# verdict: NO_CHANGE (exit 0)
# filtered ledger lists: enum_member_value_changed: InternalMode::MODE_B (private-header)
```

Without `--scope-public-headers`, or if `InternalMode` were declared directly
in `v1.h` itself (no separate private header — an enum with a confident
`PUBLIC_HEADER` origin), the same value change is reported as `BREAKING`
(`enum_member_value_changed`) — the ADR-024 override keeps genuinely
public-header enums on the surface even when unreferenced by any function
signature. See `case08_enum_value_change` and `case19` for that
non-scoped, publicly-declared-enum baseline.

## How to fix

N/A — this is the intended, compatible outcome for a value change confined
to a private-header enum with no public reachability. If `InternalMode` were
declared in the public header, or if any public function took/returned it,
the change would be reported.
