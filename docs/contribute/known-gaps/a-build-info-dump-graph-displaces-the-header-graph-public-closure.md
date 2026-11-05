---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A `--build-info` dump's L5 graph displaces the header graph, so a public-to-internal dependency goes unreported (2026-11-05)

Index: [Known gaps](../known-gaps.md)

**Status (2026-11-05): OPEN.** Measured on Linux/GCC with castxml 0.7 and
clang 18 header frontends, `python tests/validate_examples.py
--artifact-variant build-source`: case187, case188, case189 and case191 reach
their BREAKING verdict, but `public_api_internal_dependency_added` (and the
intra-version `public_to_internal_dependency`) is missing on both frontends.
The same cases report it in the `debug-headers` lane.

## Reproduction

```bash
abicheck dump libv1.so -H v1.h -o a1.json            # headers only
abicheck dump libv1.so -H v1.h --build-info old_compile_commands.json -o s1.json
# ... same for v2 ...
abicheck compare a1.json a2.json   # public_api_internal_dependency_added
abicheck compare s1.json s2.json   # missing
```

Out-of-band `--build-info`/`--sources` packs at compare time make no
difference; the dump-time `--build-info` alone loses the finding.

## Root cause

`dump --build-info` embeds a build pack (`AbiSnapshot.build_source`) whose
own L5 `source_graph` is built from the L3 build evidence plus the type folds:
record types, `TYPE_HAS_FIELD_TYPE`/`DECL_HAS_TYPE` edges, compile-unit,
option and include edges. It carries **no `SOURCE_DECLARES` edges**, the
public-header closure. The header-only graph on `AbiSnapshot.surface_graph`
does carry them (25 on case187's v2), but
`evidence_depth.resolve_l5_source_graph` prefers the pack's own graph
whenever it has one (`buildsource/embed.py` only backfills the header graph
when the merged pack has none). `source_graph_findings._internal_dependency_findings`
then finds no public closure (`_has_internal_reach_coverage` is false) and
emits nothing, so a richer evidence request loses a finding the header-only
dump makes.

## What a fix needs

Fold the header graph's `SOURCE_DECLARES` closure (and its declared public
header set) into the build pack's graph at embed time, or have the resolver
merge the two, without letting header-only edges stand in for a real L5
collection's coverage row. Owner: `abicheck/buildsource/embed.py`,
`abicheck/evidence_depth.py`.

## How it is tracked

`catalog/ground_truth.json` declares the kind for these four cases under
`expected_kinds_gaps` for both frontends in `build-source` only
(`tests/expected_kinds_gate.py`). The lane FAILs if the kind comes back
(stale declaration) or if any other kind goes missing.

`false-clean` — the BREAKING verdict holds through the structural finding,
but the named internal dependency is not reported at build-source depth.
