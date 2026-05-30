# Case 102: Frozen Runtime Signature Changed (oneTBB `detail::r1` shape)

**Category:** Runtime Contract | **Verdict:** 🔴 BREAKING

## What breaks

An `extern "C"` runtime entry point inside a contractually-frozen namespace
(`mylib::detail::r1::dispatch`) has its parameter type widened from `int` to
`long` in place. The exported symbol name is unchanged, so the binary still
links — but every consumer compiled against v1 pushes an `int` argument
into what is now a `long` parameter slot. On most calling conventions this
is silent corruption (high half-word garbage).

## Why this is a oneTBB-flavored break

[oneTBB's `VERSIONING.md`](https://github.com/uxlfoundation/oneTBB/blob/master/VERSIONING.md)
specifies that the runtime-symbol namespace `tbb::detail::r1` is
**append-only**: existing entry points are frozen at their shipped
signature forever. Any incompatible change must introduce a *new* entry
point — typically in `r2` — while keeping `r1` alive indefinitely.

This case captures the failure mode where a library author bypasses that
contract and edits an `r1` signature in place. The policy file declares
the namespace frozen so the existing `FUNC_PARAMS_CHANGED` finding cannot
be silently downgraded via a policy override.

## Code diff

| v1 | v2 |
|----|------|
| `extern "C" int dispatch(int);` | `extern "C" long dispatch(long);` |
| consumer pushes `int` → reads `int` | consumer pushes `int` → reads `long` (silent corruption) |

## How abicheck catches it

This case does NOT introduce a new ChangeKind. The existing detectors fire
as usual:

- `FUNC_PARAMS_CHANGED` — parameter type widened.
- `FUNC_RETURN_CHANGED` — return type widened.

Both are already BREAKING in `strict_abi`. The new `frozen_namespaces:`
policy field adds two things on top:

1. **Tag**: the post-processing step
   `EscalateFrozenNamespaceViolations` sets
   `Change.frozen_namespace_violation` to the matching glob pattern and
   prefixes the description with `[frozen-namespace violation: …]` so
   reviewers see the policy context.
2. **Downgrade guard**: `PolicyFile.compute_verdict` refuses any
   `overrides:` entry that would downgrade a tagged finding. A user
   cannot accidentally write `func_params_changed: ignore` and make the
   r1 contract violation invisible.

A new `namespace:` selector on `Suppression` lets users declare narrow
exemptions (e.g. for a deliberate r1→r2 migration) without falling back
to a giant regex.

## How to fix the underlying code

Don't edit `detail::r1::*` in place. Either:

1. **Add to r2**: introduce `detail::r2::dispatch(long)` as a new
   exported symbol; keep `detail::r1::dispatch(int)` alive forever as a
   thin shim that calls into the r2 entry.
2. **Wrap, don't rename**: provide an overload at the public surface
   (`mylib::run(long)`) that dispatches to the new entry; the old inline
   `run(int)` keeps calling the old `r1::dispatch(int)`.

## References

- [oneTBB VERSIONING.md](https://github.com/uxlfoundation/oneTBB/blob/master/VERSIONING.md)
- [Policy file syntax](../../docs/development/policy-files.md) (if available)
