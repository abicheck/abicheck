### Changed

- **`compare` collects a raw `--sources`/`--build-info` side in the shared resolution, not through a nested `dump`** — the CLI states the side on its `CompareRequest` (`frontends/cli/compare_inline_evidence.py`: the side's project config and its trust, the tree's `compile:` block, `scope.exclude_headers`, and the `-p`/seed/L4/changed-path execution plan) and `resolve_compare_request` collects it, the same route `run_compare_request` takes. The temporary `.abi.json` the nested `dump` wrote is gone, and `classify_resolved_pair` no longer takes `operands`/`collect_mode` overrides. `dump`'s private `_resolved_*` hooks, `resolve_dump_compile_context`'s pre-resolved-context argument, and `resolve_dump_collect_context`'s `resolved_collect_mode` argument, all of which only that nested `dump` used, are removed. What changes for a `compare` run with a raw tree:
  - `--since`/`--changed-path` now narrows the tree's L4 replay too; the nested `dump` dropped the changed-path seed.
  - The compared operand is the real binary, not a temporary snapshot: the receipt records the side's headers and compile context, and the abi3 candidate name is the operand's file name.
  - No `Snapshot written to /tmp/…` or `Storage:` lines on stderr, no second embed of the same tree, and `-v` logging is no longer reset partway through the run.
  - `config_public_header_dirs` and the compare ownership rules apply to that side, as they do to every other side.
  - Raw evidence ignored on a snapshot operand, or at a depth that collects none, is reported by resolution's note (`the old side's raw sources is ignored: …`). The `--depth source` + `hybrid` frontend rejection uses the shared message, which now names `compile.frontend: castxml in .abicheck.yml`.

### Fixed

- **`--depth source` now catches a public macro change on every route** — L4 source replay under the default `auto` frontend runs clang, its documented default and the only extractor that records macros and inline/template/constexpr bodies, instead of L2's castxml. Before, a `#define` value change went unseen on a C++ tree (exit 4 without `public_macro_value_changed`), and on a C tree it surfaced only because castxml failed there and `dump` re-embedded the evidence with clang. A stated frontend (`compile.frontend`, `ABICHECK_AST_FRONTEND`) still selects the L4 extractor.
- **castxml's L4 replay parses C translation units** — it now runs a C unit in castxml's C emulation (`--castxml-cc-gnu-c`), as the L2 header parse already did, instead of failing with `-std=gnu++17 not allowed with 'C'`.
