# case85 — internal function-template signature leaks via public API (BREAKING)

## What this case demonstrates

The function-template analogue of PR #238's type leak detection. A
helper template lives in `lib::__detail::` — conceptually private —
but a public inline algorithm dispatches to it, so every consumer's
symbol table contains an instantiation of the "internal" template.

| v1 ships | v2 ships |
|---|---|
| `__detail::walk<int>`, `__detail::walk<float>` | `__detail::walk<int>`, `__detail::walk<double>` |

A consumer that wrote `lib::sum_range<float>(...)` linked against v1
ends up needing `__detail::walk<float>`; against v2 that symbol no
longer exists.

## Why a dedicated finding

A plain `func_removed` for `__detail::walk<float>` invites the dismissal
"that's internal, ignore it". `INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`
makes the leak path explicit and aggregates the per-instantiation
churn into a single finding per stem.

## Expected verdict

`BREAKING` — consumers fail to link or load.
