---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - template-library-contract
summarizes:
  - bundle-analysis
  - evidence-model
depends_on:
  - abicheck/bundle_manifest.py
  - abicheck/buildsource/source_abi.py
  - abicheck/buildsource/source_replay.py
lifecycle: active
generated: false
---

# Template- and Header-Heavy Libraries

## What a template library exports

A class template exports nothing. `Buffer<T>` is a recipe; a symbol exists
only when someone instantiates it, and an *implicit* instantiation is
compiled into the consumer's binary, where the library never sees it. The
only symbols the library owns are its *explicit* instantiations — the
`template class Buffer<int>;` lines its build emits — and those carry the
same hazard every other symbol does, plus one of their own: adding a field
keeps the mangled name identical while `sizeof(Buffer<int>)` grows, and a
consumer that stack-allocated the old size is smashed with no header-level
signal. [Part 4 § Templates and inline](abi-series/04-cpp-abi.md#3-templates-and-inline)
is the mechanism; this page is what to do about a library that is *mostly*
this.

## The contract is the instantiation matrix

For a template library the public contract is not a header and not an
export table. It is the *matrix* of explicit instantiations the build
system enumerates — every `(Float, Method, Task)` triple the release
promises to have compiled — and a comparison that does not know the matrix
cannot tell a promised instantiation from an incidental one. The
instantiation manifest states it directly:

```yaml
version: 1
provides:
  - template: oneapi::dal::train_ops
    instantiations:
      - {Float: float,  Method: "method::dense",  Task: "task::train"}
      - {Float: float,  Method: "method::sparse", Task: "task::train"}
      - {Float: double, Method: "method::dense",  Task: "task::train"}
      - {Float: double, Method: "method::sparse", Task: "task::train"}
    library: libonedal_core.so.1
    optional_provider: false
```

Dozens of entries describe thousands of mangled symbols, and each one the
new release stops exporting is a broken promise rather than one more
removal in a long list. Two cases show the two ways the matrix breaks: the
instantiation is still exported but its layout changed
([case17](../reference/examples/case17_template_abi.md)), and the
instantiation is simply missing from the shipped binary because the build
dropped a line
([case79](../reference/examples/case79_missing_template_instantiation.md)).
Parameter order in each entry must match the template's own; the shapes,
matching rules and the bootstrap script that produces a first manifest
from a release are owned by
[Multi-binary § `--manifest`](../use/multi-binary.md#-manifest-path-experimental),
and the manifest is applied on a product comparison
([Products, Not Libraries](products-not-libraries.md)):

```bash
abicheck compare release-1.0/ release-2.0/ -H include/ --manifest manifest.yaml
```

## What the header side can and cannot see

The header tier sees a template through the compile context it was given.
The default castxml backend emits template *instantiations* only, never
the uninstantiated pattern; the clang backend records the pattern as well
([Header Backend Capabilities](../reference/header-backend-capabilities.md)).
Neither backend detects a change to an *uninstantiated* template's
signature: nothing was instantiated, so nothing was compared. That is the
source tier's job — L4 replays the declaration itself
([case122](../reference/examples/case122_template_signature_uninstantiated.md),
the same case [How a Break Shows Up](how-a-break-shows-up.md) cites for
"the source changed, no binary did"). Three neighbours are visible at the
header tier and worth knowing by name: a default template argument
changed, which silently changes what every consumer instantiates
([case87](../reference/examples/case87_default_template_arg_changed.md));
an internal template's signature changed while public code depends on it
([case85](../reference/examples/case85_internal_template_signature_changed.md));
and a `detail::` templated base class whose layout change propagates into
every public derived class
([case77](../reference/examples/case77_detail_templated_base_changed.md)).
Which tier each break needs is defined by
[Evidence & Detectability](evidence-and-detectability.md).

## The cost cliff

The one cost cliff in the evidence ladder is at the source tier, and it
tracks template depth: a single template-heavy translation unit's AST dump
can reach gigabytes, so a full-target replay of a large tree costs hours
and memory, while a replay *seeded* by the changed translation units costs
minutes. The replay caps its worker count by available memory, including a
container's cgroup limit, rather than by CPU count alone. Ask before you
spend:

```bash
abicheck scan libfoo.so -H include/ --sources . --depth source \
  --since origin/main --dry-run
```

The dry run prints the translation units the seed selects and the
projected per-layer cost without scanning. In CI, `--budget` fails loudly
on overflow rather than shrinking scope, so a scan that finished is a scan
that did what it claims. Numbers, knobs and the reasoning behind the
memory cap are owned by
[Performance § L4 source-replay performance](../contribute/performance.md#l4-source-replay-dump-side-performance);
the moments to run the seeded versus the unseeded scan by
[Where in the Pipeline](where-in-the-pipeline.md).

## Multi-TU surfaces and comparability

A template library's public surface is rarely one header. `--dump-manifest`
describes the translation units that together form one side's snapshot —
each with its own compile context — and is side-scoped, so old and new can
carry different manifests. That is also where the comparability gate
earns its keep: two snapshots extracted under different manifests, flags
or scope settings differ in thousands of instantiations that are not
contract changes, and the gate refuses the pair with no verdict rather
than producing a page of phantom additions and removals. "Not comparable"
is the better answer; rebuild one side under the other's profile, or pass
`--diagnostic-comparison` to see the tentative diff with its assurance
stamped as none. The fingerprint and its carve-outs are owned by
[Build Profile Comparability](build-profile-comparability.md); the
manifest flag by the [CLI Reference](../reference/cli-reference.md).

## Header-only libraries

A header-only library is the limit case: every function is inline or a
template, the export table is empty, and the whole contract is the inline
bodies and the types they name. The evidence question is then the header
graph rather than any binary — a public struct that gains a field of a
private type is a break with no symbol anywhere
([case191](../reference/examples/case191_header_only_graph_field_type.md)).
[Static & Header-Only Contracts](static-and-header-only.md) is the shape
without a binary.

---

**Ladder:** ← [Products, Not Libraries](products-not-libraries.md) · Tier 7 · At scale · [Dependency & Runtime Floors](dependency-floors.md) →
