---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `compare --bundle-facts-out` stays a `compare` flag because `dump` has no release fan-out

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** --bundle-facts-out is still a compare option (frontends/cli/options/bundle_facts_library_manifest.py, commands/compare.py), and no dump command offers a bundle-facts format/fan-out. The blocker is removed, but the dump fan-out is unwritten.


`one-comparison-product.md`'s own framing, recorded rather than fixed here:
`--bundle-facts-out` asks a *comparison* to capture a baseline. Capturing
the OLD side's per-library snapshots into a `BundleFacts` document is a
**dump** operation — it reads one release and writes facts about it, and it
does not need a NEW side at all. It lives on `compare` only because that is
the one command that knows how to turn a directory or package into a set of
per-library snapshots; `dump` takes exactly one artifact, and a directory
operand there is interpreted as a stored `ProjectSnapshot` *package*, not as
a release to fan out over.

**The two options, and why neither landed in this pass.** Adding the `dump`
fan-out is the right shape: `abicheck dump RELEASE_DIR --format bundle-facts`
would make baseline capture a first-class dump, `compare` would consume the
document it already consumes, and `--bundle-facts-out` could be retired
rather than reimplemented. What it needs is the whole release input-resolution
chain — package/debug-package/devel-package extraction, library discovery and
canonical-key matching, stored-`ProjectSnapshot` variant materialization,
`--dso-only` classification — and every one of those functions is currently
`frontends`/flat-`cli_*`-classified (`retired cli_compare_release_matrix.
_prepare_compare_release_inputs` and its helpers), with `click.UsageError`
raises inside them. `dump`'s own execution path is `workflows`-classified, so
the `engine-cli-boundary` gate forbids it from reaching any of that, and the
fan-out cannot be written without either duplicating the chain or moving it.

That move is the *same* work `one-comparison-product.md` item 4 asks for on
its own terms (relocating `frontends/cli/release_compare_request.py`'s call
chain into `workflows` with typed errors), which is why the ordering here is
a real dependency rather than a deferral: the `dump` fan-out is a
straightforward composition once that chain is engine-side, and an
open-coded duplicate of it before then is exactly the second parallel path
this repository's architecture rules forbid.

**That dependency is now satisfied** (same PR): the chain is
`abicheck/workflows/release_inputs.py`, it raises the typed
`errors.ReleaseOperandError`, and `abicheck.service.resolve_release_compare`
reaches it with no Click context. So the `dump` fan-out is no longer
*blocked* -- it is simply not written yet, and writing it is a new command
surface (`dump`'s own operand arity, `--format bundle-facts`, the ADR-054
root-surface bar for any new spelling, and the retirement path for
`--bundle-facts-out`) rather than a refactor. Recorded here as the next
slice, with the blocker removed, instead of left reading as unreachable.

**Until then, `--bundle-facts-out` stays where it is, and stays honest about
what it is:** a capture ridden on a comparison. It is not reimplemented, not
widened, and not given a second spelling. When the `dump` fan-out lands, the
flag becomes a deprecated alias for it rather than a separate producer —
`abicheck/retired cli_compare_release_helpers.py`'s `write_bundle_facts_out` already
takes the already-resolved per-library snapshots and nothing else, so it is
the reusable half and moves as-is.
