<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A labeled `--include old:LABEL=PATH`/`new:LABEL=PATH` (ADR-050 D1) was
  silently dropped when combined with a raw `--old/new-sources` tree or
  `--build-info`** — `cli._embed_inline_source_side`'s own nested `dump`
  invocation had no way to receive the resolved `include_labels` map, so the
  inline-dumped temporary snapshot for that side always fingerprinted its
  support root as unlabeled/external, even though the non-inline path
  already threaded the same label correctly. `dump_cmd` gains a private
  `_resolved_include_labels` hook (matching the existing
  `_resolved_compile_context`/`_resolved_collect_mode` pattern),
  `perform_elf_dump` gains an `include_labels` parameter forwarded to
  `dumper.dump()`'s `extra_include_labels`, and both of
  `run_compare`'s `_embed_inline_source_side` call sites now pass
  `include_labels` through. Found via CodeRabbit review.
