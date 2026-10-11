---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# An `-I`-reached sibling library's headers yield LOW-confidence export obligations; release reconciliation and JSON confidence not yet narrowed

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The dependency include-root case is closed (extract/public_root_ownership.py) and the PVXS case is narrowed to LOW confidence via workflows/ownership_request.py:136 with_target_roots. The entry's own 'Still open' list remains: per-finding confidence is missing from JSON findings, policy/release_contract_reconciliation.py:237 still emits HIGH confidence without the narrowing, and umbrella headers get LOW-confidence findings.


> **Update (2026-10-01): the PVXS case is narrowed, per the maintainer's
> ruling.** Recording the declared `-H` set turned out to need no
> `SCHEMA_VERSION` bump: ADR-075's `extraction_scope.ownership_rules.
> target_roots` (schema v52) already persists the run's target roots. It
> took only `-H` *directories*, so a run naming files recorded no root and
> every declaration read `unresolved`/`no_root` alike.
> `workflows.ownership_request.with_target_roots` now makes every existing
> `-H` entry a root (a file covers exactly itself). A declaration no root
> covers -- reached only through `#include` and an `-I` root -- is still
> reported as `public_not_exported`, but at LOW confidence and worded as
> *not established* (`buildsource/export_obligation_ownership.py`), and the
> check's coverage detail counts them. The declared header's own missing
> exports keep HIGH confidence (the positive control), and a `-H`
> *directory* declares everything under it, so nothing changes there.
> `tests/test_include_root_export_obligation.py` pins both, with the
> reported-symbol set checked against the fixture's undefined declarations
> so nothing can be dropped.
>
> **Still open.** (1) Per-finding `confidence` is not on the JSON wire
> format, so a JSON reader sees the narrowing only in the description and
> the check detail, not as a field. (2) The release surface's obligations
> (`policy.release_contract_reconciliation`) do not apply the narrowing;
> in a multi-member release the union of exports satisfies a sibling's
> declarations anyway, but a declaration *no* member exports is still
> reported at full strength. (3) An umbrella header (`-H foo.h` including
> the library's own `foo/bar.h`) now gets LOW-confidence findings for
> `bar.h` -- the accepted cost; pass the directory as `-H` to declare it.

> **Partially closed (2026-09-16).** An `-I` root now widens public
> provenance only where the run's own *declared* public headers live
> underneath it (`extract/public_root_ownership.py`, applied by
> `provenance.public_dirs_with_owned_roots` and, identically,
> `buildsource/header_graph.py`). That is a fourth route none of the three
> candidates below considered, and it needs no new `ScopeOrigin` member, no
> `Fact` retightening and no `SCHEMA_VERSION` bump: the evidence to separate
> the two questions was already in hand at classification time, in the
> declared `-H`/`--public-header-dir`/`sources.public_headers` set the
> caller passes alongside the `-I` list.
>
> A root the rule declines is classified `UNKNOWN`, **not**
> `PRIVATE_HEADER`. That distinction is the whole safety argument and was
> not in the first version of the fix: `PUBLIC_HEADER` is what creates an
> export obligation, so a dependency's declarations must not have it; but
> `PRIVATE_HEADER` is a *confident* signal that public-surface scoping acts
> on to drop findings, so a root the run merely could not place must not
> have it either, or a library whose own public headers are split across
> include roots loses real breaking changes from its verdict. Raised as a
> P1 by Codex's security review on the PR; four attempted repros were each
> caught by some other mechanism (the undeclared-export removal exemption,
> the export-table closure, the conservative-unknown fallback), but the
> safety of a demotion rule must not rest on unrelated mechanisms
> happening to cover it.
>
> **What it closes:** a *dependency's* include tree. Intel MKL passes an MPI
> include directory solely so `mkl_cdft.h` can parse `#include <mpi.h>`; no
> MKL public header lives under it, so it no longer widens anything, and the
> 2,211 `MPI_*`/`PMPI_*`/`QMPIX_*` `public_not_exported` findings are gone.
> Regression tests: `tests/test_dependency_include_root_ownership.py`,
> including a property-style statement of the containment predicate.
>
> **What it does not close, and this is the reported PVXS case above.**
> `-H include/pvxs/iochooks.h` with `-I <pvxs>/include` is containment:
> the declared header lives *under* that root, so the root keeps its
> widening power and `version.h` beside it is still `PUBLIC_HEADER` — still
> charging libpvxsIoc for libpvxs's exports. Containment separates "a
> dependency's tree" from "the library's own tree"; it cannot separate *two
> sibling libraries sharing one include tree*, because from one snapshot's
> point of view those are the same tree. Everything below still applies to
> that case, and the three candidate fixes are still the options — the
> narrowest (recording the declared `-H` set on `AbiSnapshot`) remains the
> most promising, since "which headers this run declared" is exactly the
> evidence a shared tree makes unrecoverable at classification time.
>
> The positive control the entry demands is kept and tested: a symbol
> declared in a header that *is* in `-H` and genuinely absent from the
> binary still reports.


**Reported on a real PVXS build** (abicheck `3737f9f9`, CastXML 0.7.0, EPICS
Base 7.0, gcc 13, `-std=c++11`), and reproduced by comparing one snapshot
against *itself* — byte-identical operands, verdict `NO_CHANGE`:

```
abicheck dump lib/linux-x86_64/libpvxsIoc.so.1.5 \
  -H include/pvxs/iochooks.h \
  -I <pvxs>/include -I <epics>/include ... --depth headers
```

`-H` names exactly one file. `include/pvxs/version.h` is reachable only
because `iochooks.h` `#include`s it and `-I <pvxs>/include` makes it
findable; the symbols it declares (`pvxs::version_int()`,
`version_str()`, `version_abi_int()`) are exported by **libpvxs**, a
different library. The comparison charged **libpvxsIoc** with three
`public_not_exported` findings for them.

**Root cause, confirmed by reading both halves.**
`provenance.apply_provenance` deliberately folds the `-I` roots into the
public-*directory* set once a real `-H` set has opted classification in
(`provenance.py`, the `include_search_dirs` parameter and
`_public_dirs_from_include_roots`). That fold exists for a real defect it
fixed: without it every transitively-`#include`d header classified
`PRIVATE_HEADER`, and a genuine breaking layout change reached only through
an umbrella header silently dropped out of the compared surface. So the
declaration's `ScopeOrigin` becomes `PUBLIC_HEADER`, and
`buildsource/cross_source_checks.py`'s `_has_export_obligation` /
`_var_has_export_obligation` gate on exactly `origin == PUBLIC_HEADER`.

The two questions are being conflated:

1. *Is this declaration in the component's compared public surface?* — the
   fold's answer (yes) is defensible: an API change to it matters.
2. *Does **this binary** owe an exported symbol for it?* — the fold's answer
   is not implied by (1) at all, and for a shared include tree serving
   several libraries it is wrong.

Note the reported case used a `-H` **file**, so this is not the documented
"a directory entry tags everything under it public" behaviour; a directory
`-H` root reaches the same place through the same fold.

**Not fixed here, and why.** The evidence needed to separate (1) from (2) —
which headers the run's own `-H` set *declared*, as opposed to which the
`-I` widening admitted — is not recorded anywhere a check can read it after
serialization. `ScopeOrigin` collapses both to `PUBLIC_HEADER`, and
`ExtractionContract` keeps fingerprints rather than paths. The three
candidate fixes each carry real blast radius:

- **A new `ScopeOrigin` member** (`INCLUDE_CONTEXT_HEADER`): 52 existing
  `ScopeOrigin.PUBLIC_HEADER` sites must each decide whether they mean
  "in the compared surface" or "owned by this component", and the value is
  persisted vocabulary.
- **Tighten `in_public_contract_fact` to the declared set** (already
  persisted, schema v46): but `compare/export_transition.py` already keys on
  `is_confirmed_false(in_public_contract(...))` to decide *not* to suppress a
  removal, so a confirmed-false for widened declarations would add findings
  elsewhere.
- **Record the declared `-H` files/dirs on `AbiSnapshot`** and re-classify in
  the check: the narrowest of the three, and independently useful (a stored
  baseline records nothing about the contract it was dumped under), but it is
  a `SCHEMA_VERSION` bump inside ADR-050's comparability contract.

Whichever is taken, the fix must keep the positive control: a symbol declared
in a header that *is* in `-H` and genuinely absent from the binary must still
report. A suppression rule or a per-project carve-out is explicitly not the
answer.
