### Changed

- **Compiler-driver resolution has one owner, `abicheck.extract.compiler_driver`.**
  Which clang binary a header dump, source extractor, S2 pre-scan, dump cache
  key, or frontend preflight runs for a given `--compiler`/`--compiler-prefix`,
  and the SYCL mode (`-fsycl`/`-fno-sycl`, single-pass host-only pinning, DPC++
  multi-context acquisition) that depends on it, are now answered by that module
  alone. The retired private paths are removed without re-exports: the
  `abicheck.dumper` and `abicheck.dumper_clang` driver helpers
  (`_resolve_clang_bin`, `_needs_sycl_host_only`, `_is_dpcpp_family_binary`, and
  related names) now live in `compiler_driver` under public names
  (`resolve_clang_bin`, `needs_sycl_host_only`, `resolve_dpcpp_acquisition`,
  `is_clang_family_binary`, and so on). `abicheck.dumper` no longer re-exports
  `_resolve_header_backend`; its owner is `abicheck.extract.header_ast_backend`.
- **The DPC++ single-pass probe moved to `abicheck.extract.dpcpp_jobs`** from
  `abicheck.buildsource.dpcpp_jobs`, because it decides the SYCL mode that
  `compiler_driver` owns. Its replay-planning functions are unchanged.
- **L4 source-ABI replay resolves its clang driver through
  `abicheck.workflows.extraction.resolve_l4_replay_clang_bin`.** It replaces the
  `resolve_source_frontend_clang_bin` re-export that `workflows.extraction` used
  to carry; frontends call that workflow function rather than reaching into
  `extract`. Behavior is unchanged: CL-mode drivers stay eligible for replay.
