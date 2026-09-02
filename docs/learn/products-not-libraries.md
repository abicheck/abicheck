---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: advanced
canonical_for:
  - bundle-analysis
summarizes:
  - project-integration
depends_on:
  - abicheck/bundle.py
  - abicheck/bundle_manifest.py
  - abicheck/bundle_facts.py
  - abicheck/cli_aggregate.py
lifecycle: active
generated: false
---

# Products, Not Libraries

## A product is one contract

A release that ships several shared libraries is not several contracts; it
is one. `libalgo.so` imports `core_mul` from its sibling `libcore.so`, so
the symbol is public *inside the product* whether or not any user ever
calls it: remove it from `libcore` and a per-library comparison says
`libcore` broke and `libalgo` is unchanged, which is true and useless — the
product no longer loads. Three things about a product cannot be seen one
library at a time:

- **Which library provides a symbol.** A symbol that moves from `libcore`
  to `libutil` is a removal in one report and an addition in another;
  seen as a product it is a *provider change*, compatible for a consumer
  that links both and a break for one that links only `libcore`.
- **The SONAME cohort.** Libraries versioned together (`libfoo_core.so.3`,
  `libfoo_algo.so.3`) promise to move together; one member bumping its
  SONAME alone is a skew no single report can call.
- **What the release promises.** An explicit instantiation the build
  system emits is either part of the contract or an accident of the
  compiler, and only a declaration of the promised set can tell the two
  apart.

The bundle layer adds these cross-library findings on top of the
per-library ones and never hides any of the latter; the product verdict
is the worst of both.

## Three shapes

| Your libraries… | Shape | Checked as | Declared with |
|---|---|---|---|
| depend on one another and ship as one release | release bundle | one comparison, one report, cross-library findings | `.abicheck.yml` `bundles:` grouping the member targets — [S14](../integration/scenarios/release-bundle.md) |
| are built together but do not depend on one another | independent targets | one comparison per target, no cross-library claim | `targets:` only — [S15](../integration/scenarios/multi-dso-project.md) |
| live in one repository with separate release cadences | monorepo components | per-component targets, each with its own baseline channel | [S25](../integration/scenarios/monorepo.md) |

The declarative form is the [project integration](../integration/concepts.md)
layer; each scenario page carries the exact YAML, and this page does not
restate it. One rule worth knowing before choosing: a bundle check in the
declarative topology is binary-depth only, because a bundle baseline stores
its members' staged binaries and no per-member header or build evidence
([S14 § Depth is binary-only](../integration/scenarios/release-bundle.md#depth-is-binary-only-by-design)).
A member that needs header or source depth declares its own check beside
the bundle's.

## Run it

Two directories are a product comparison:

```bash
abicheck compare release-1.0/ release-2.0/ -H include/ --fail-on-removed-library
```

Every shared object discovered in both trees is compared as a pair, a
library present only on the old side is reported as removed (exit 8 with
the flag above), and the cross-library pass runs over the whole set. The
JSON report carries the per-library results under their library names and
a `bundle` block for the cross-library findings and the bundle verdict;
Markdown renders the latter as a "Bundle (Cross-Library) Findings" section.
Field names and the exit-code table are owned by
[Multi-binary § JSON output schema additions](../use/multi-binary.md#json-output-schema-additions).

## The five cross-library findings

- **SONAME skew** — one member of a declared cohort changed its SONAME
  while its siblings kept theirs. Declared with `--bundle-cohort`; fixture
  [`case84_bundle_soname_skew`](https://github.com/abicheck/abicheck/tree/main/examples/case84_bundle_soname_skew).
- **Intra-bundle dependency removed** — a symbol one sibling imports from
  another disappeared from the provider. The consumer library is
  byte-identical and per-library clean; the finding lands on *it*, with
  the provider named (`case90`).
- **Intra-bundle signature drift** — an `extern "C"` function kept its
  name and changed its parameters, so the consumer's unchanged call now
  passes the wrong arguments (`case91`).
- **Provider changed** — a symbol moved between siblings and the bundle
  still exports it exactly once: risk, not a break, and only a product
  view can say so (`case92`).
- **Manifest drift** — an instantiation the release *promised* is no
  longer exported. Per-library this is one more `func_removed` among many;
  against the manifest it is a broken promise (`case93`).

The four bundle fixtures have no generated case pages; they are described
in [Multi-binary § References](../use/multi-binary.md#references).

## Declaring what the bundle promises

`--manifest` names the symbols the release publicly promises, in three
entry shapes: a `pattern:` over demangled names (one line freezes a
namespace), `template:` with an `instantiations:` matrix (the shape a
template library needs — [Template- and Header-Heavy Libraries](template-heavy-libraries.md)
is about exactly that), and a literal `symbol:`. Two more flags shape the
resolution graph: `--bundle-system-providers` for libraries the bundle
imports from but does not ship (libc, libstdc++ and their kin are built
in; add your own), and `--bundle-cohort` for the SONAME cohort above. The
file format, verdict rules and a bootstrap script that produces a first
over-broad manifest from a release are owned by
[Multi-binary § `--manifest`](../use/multi-binary.md#-manifest-path-experimental).

## Comparing against stored facts

A live two-directory comparison needs both releases' binaries on disk.
Capture the old side's bundle facts once, then compare later releases
against the stored document without reopening the old binaries:

```bash
# release 1.0 -> 2.0, live on both sides, and persist 1.0's facts
abicheck compare release-1.0/ release-2.0/ -H include/ --bundle-facts-out bundle-1.0.json

# release 1.0 -> 3.0, without release-1.0/ on disk
abicheck compare bundle-1.0.json release-3.0/ -H include/ --old-bundle-facts
```

A product whose members share one include tree but not one toolchain gives
individual libraries their own header root or compile context through
`--bundle-facts-library-manifest`
([Multi-binary § Comparing against a stored bundle baseline](../use/multi-binary.md#comparing-against-a-stored-bundle-baseline-g38-phase-2)).

## Fan-out and fan-in

In CI a product runs one check per target and folds the reports into one
gate with `aggregate`. The fold has to know which reports it *expected*:

```bash
# one --build-output per contract profile the checks: block names
abicheck project plan .abicheck.yml \
  --build-output linux-gcc=abicheck-build/linux-gcc -o plan.json
abicheck aggregate reports/ --run-plan plan.json
```

The plan resolves every `checks:` entry against each named profile's
build output, which is why it needs one `--build-output` per profile: a
profile with no build output cannot resolve a check, and a plan that
resolves no check at all exits 1 rather than emitting an empty target set
that would let the fold pass having checked nothing (`--allow-empty` is the
deliberate opt-in for a bootstrap run). Without a declared target set — `--run-plan` from the project plan (the
declarative form), or a hand-written `--manifest` — a missing report and an
intentionally absent one look identical, so a bare `aggregate reports/`
exits 64 rather than guess; `--discovered-only` is the explicit opt-out
that gates on whatever is present. A report that never arrived must be a
failure, not an absence: a job that crashed before writing its report
would otherwise pass the product. The five aggregation axes and the gate
block are owned by [Aggregate Reports](../use/aggregate-reports.md); the
topology schema by the
[Project Targets Schema](../reference/project-targets-schema.md).

## What the bundle layer cannot do today

- **ELF only.** The resolution graph is built from `DT_NEEDED` edges and
  GNU version sections; on PE and Mach-O the cross-library pass is skipped
  and only per-library results are reported
  ([Multi-binary § Platform support](../use/multi-binary.md#platform-support)).
- **Binary depth in the declarative topology.** A bundle check in
  `.abicheck.yml` runs at binary depth; header and source evidence are per
  member, through the member's own check (S14, above).

---

**Ladder:** ← [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) · Tier 7 · At scale · [Template- and Header-Heavy Libraries](template-heavy-libraries.md) →
