---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - public-surface
---

# Public surface and scoping

Most "this reported dozens of breaks" reports are a scoping problem, not a
detector problem: the tool was shown the whole export table (internal
helpers, inlined template instantiations, standard-library instantiations,
dependency types) instead of the surface the library actually promises.

What counts as the public ABI surface, and why a symbol being exported does
not make it public, is owned by
[the ABI surface page](../../docs/learn/abi-surface.md).

## The scoping dials

| Dial | What it does |
|---|---|
| `--scope-public-headers` / `--no-scope-public-headers` | restrict the surface to declarations reachable from the public headers |
| `--header` / `-H` | name the public headers explicitly |
| `--public-symbol` (repeatable), `--public-symbols-list FILE` | pin an explicit public symbol set when headers cannot express it |
| `--contract public\|exports\|all\|auto` | ask for contract-relevance decisions, and choose the evidence domain they are judged against |
| `--include-dependencies` | opt **out** of the default dependency-header scoping and keep the full transitive surface |

`dump` and `compare` scope out declarations whose own defining header is a
toolchain or system header by default. That is a header-*origin* filter, not
an ABI-visibility one: the library's own private declarations are still kept.

## `--contract`

`--contract` does both jobs at once: naming a domain is what turns per-finding
contract-relevance classification on, and which value you name is the evidence
domain each finding is judged against.

- `public` — the public-header surface.
- `exports` — the binary's observed export table.
- `all` — no domain restriction.
- `auto` — evaluate, but leave the domain to the precedence chain below an
  explicit CLI value: `--scope-public-headers`/`--no-scope-public-headers`,
  then the project's `.abicheck.yml`. Use it when the domain is a project
  decision already recorded elsewhere.

Omit the flag entirely and nothing about the run changes.

This is **not** cosmetic. Under `--contract` the relevance decision
runs before compatibility policy, so the selected domain can change the finding
set, the verdict, and the exit code. Each finding gains
`contract_relevance`, `contract_reason_code`, `contract_assurance`, and
`compatibility_decision`; the report gains `contract_coverage_failures` and
`contract_coverage_exit_contribution`.

Contract coverage is an **orthogonal exit axis**: incomplete evidence for the
selected domain contributes `1`, folded with `max`, so it can raise a clean
`0` to `1` but never lowers a real `2`/`4`. It is unsuppressible by design —
carry it into your summary
([safety-invariants.md](safety-invariants.md) item 5).

The full model is owned by
[contract-aware compatibility](../../docs/learn/contract-aware-compatibility.md)
and the task page
[contract evaluation](../../docs/use/contract-evaluation.md).

## Reading the scoping outcome

When public-header scoping was requested the report carries a `scope` object.
Its `resolved` field is the one that matters: when `false`, the public surface
could **not** be determined and the run fell back to the full export table —
`manual_review_required` is then `true` and the result is unconfirmed. A
report with `scope.resolved == false` must never be summarized as a clean
scoped result.

## Triaging a sudden flood of findings

1. Check `scope.resolved` and whether scoping was requested at all.
2. Check whether the two sides used the same toolchain — a compiler or
   standard-library upgrade regenerates template instantiation symbols
   wholesale. See
   [compiler-and-build-profiles.md](compiler-and-build-profiles.md).
3. Group before reading: `--report-mode root-cause`. See
   [root-cause-grouping.md](root-cause-grouping.md).
4. Only then consider whether a policy or suppression is appropriate — and
   never author one just to quiet output
   ([policies-and-suppressions.md](policies-and-suppressions.md)).
