### Fixed

- **`compare` no longer lets old and new snapshots silently disagree on the
  C++ standard.** `dumper.py`'s C++20 `requires`/`concept` auto-detection
  heuristic only ever inspected one side's headers at a time, so an old/new
  pair could resolve to different dialects when neither side pinned an
  explicit `-std=` (e.g. only the *new* side picked up a `concept` and got
  silently upgraded to `-std=gnu++20` while *old* stayed on the toolchain
  default). New `cli_helpers_compare._pair_wide_dialect_override` (wired
  into `compare`'s CLI path) and a matching fix in
  `service.run_compare_request` (the Python-API/MCP path) resolve the
  dialect once, over the union of both sides' headers, and pin the identical
  explicit `-std=gnu++20` onto both — an explicit user-supplied standard
  still always wins.
- **Compile-database ABI flags (`-m32`/`-m64`/`-march=`/`-stdlib=`/enum &
  char-signedness layout flags) now reach the real `dump -p`/`compare -p`
  header parse, not just the separate build-evidence-drift diff.**
  `build_context.py`'s `_ABI_EXTRA_PREFIXES` previously forwarded only a
  narrow subset (`-fabi-version=`, `-fpack-struct=`, `-fms-extensions`,
  `-frtti`/`-fno-rtti`, `-fexceptions`/`-fno-exceptions`) from a matched
  `compile_commands.json` entry into the actual castxml/clang invocation via
  `to_castxml_flags()`; data-model and calling-convention flags a real build
  used were captured for `buildsource.adapters.base`'s advisory
  `ABI_RELEVANT_BUILD_FLAG_CHANGED` diff but silently dropped from the
  header parse itself — reintroducing "header parse drift" for exactly the
  ABI-relevant flags a matched build entry is supposed to guarantee.
- **`--allow-unsupported-castxml` is now a real CLI flag** (`dump`/`compare`/
  `scan`, alongside `--allow-ast-frontend-fallback`), not only the
  `ABICHECK_ALLOW_UNSUPPORTED_CASTXML` environment variable the CastXML
  version-gate override previously required — `model.py`'s snapshot
  provenance docstring already documented a `--allow-unsupported-castxml`
  flag that did not actually exist as a CLI option.
