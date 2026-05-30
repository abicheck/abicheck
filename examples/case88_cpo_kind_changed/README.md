# case88 — CPO kind changed (BREAKING)

## What this case demonstrates

A public name `lib::sort` is published in v1 as a free function and in
v2 as a *customization point object* (constexpr variable of an
unspecified function-object class type).

| v1 declares | v2 declares |
|---|---|
| `void lib::sort(int*, int*)` (function) | `constexpr lib::__sort_fn lib::sort` (variable) |

Call syntax `lib::sort(a, b)` keeps working unchanged. What breaks:

- `decltype(lib::sort)` changes from a function type to a class type
- extern templates parameterised on the function type stop compiling
- trait specializations keyed on the function type silently miss
- code that took the function's address fails to compile

## Why a dedicated finding

A plain `func_removed` + `var_added` reports the two halves but loses
the connection. `CPO_KIND_CHANGED` names the pattern explicitly so
reviewers see "this is the CPO migration" rather than two unrelated
symbol churn lines.

## Expected verdict

`BREAKING` — every consumer that did anything more than call-syntax
with `lib::sort` must be updated.
