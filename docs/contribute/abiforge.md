---
doc_type: contributor
audience:
  - contributor
level: intermediate
lifecycle: active
generated: false
depends_on:
  - abiforge/coverage.py
  - tests/test_abi_edit_corpus.py
---

# ABIForge

ABIForge tests ABI checkers against controlled changes to C and C++ libraries.
A seed supplies a small library. An edit operator changes one aspect of its
interface and declares the expected findings and verdict range. The harness
builds both versions and compares the checker's answer with that expectation.
Where a case provides a consumer, compiling or running it supplies an
independent observation of the effect on callers.

## Reproduce one edit

From an abicheck checkout with the test dependencies, GCC, G++ and CastXML
available on Linux, run this single case. Use a fresh output directory so
the report covers just this run.

```bash
results=$(mktemp -d)
ABICHECK_EDIT_CORPUS_COVERAGE_DIR="$results" python -m pytest -q \
  'tests/test_abi_edit_corpus.py::test_edit_produces_declared_ground_truth[enum_append_member-c_core-gcc]'
python -m abiforge.coverage report "$results"
```

The edit appends an enumerator and preserves the existing values. In the
per-case JSON, compare `expected` and `produced`: both should contain
`ENUM_MEMBER_ADDED`. Check `status` before interpreting the verdict.
A record marked `unavailable` identifies a missing prerequisite. A run
marked `ran` records the observed verdict and the compiler configuration.

## Interpret a campaign

Each result applies to a seed, edit, toolchain and evidence configuration.
An operator can also specify the affected member, values and number of
findings. This catches a checker that reports a break but attributes it to
the wrong field. When the supplied evidence cannot establish a detail,
the result remains evidence-limited.

Use the [corpus README](https://github.com/abicheck/abicheck/blob/main/abiforge/README.md)
for the PR and nightly matrices and the [comparison receipts](https://github.com/abicheck/abicheck/tree/main/abiforge/receipts/competitors)
for recorded cross-tool runs. For observations on installed SDKs and
unchanged clients, see [Measurements beyond the example catalog](../reference/tool-comparison.md#measurements-beyond-the-example-catalog).
