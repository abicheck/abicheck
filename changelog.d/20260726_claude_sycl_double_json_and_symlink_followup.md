<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--ast-frontend clang` + `-fsycl` on Intel's oneAPI driver produced two
  concatenated JSON documents on stdout, breaking `json.load()`**
  (`abicheck/dumper.py`, `abicheck/dumper_clang.py`,
  `abicheck/dumper_clang_errors.py`): a bare `-fsycl` makes Intel's oneAPI
  DPC++/C++ driver (`icx`/`icpx`/`dpcpp`/`dpcpp-cl`) run *two* separate
  `-cc1` passes for one compile — a SYCL device-side pass
  (`-fsycl-is-device`) and a SYCL host-side pass (`-fsycl-is-host`) — each
  with its own `-Xclang -ast-dump=json`, both writing a complete JSON
  document to the same stdout stream abicheck captures, back-to-back with no
  separator. `json.load()` parsed only the first document and raised
  `Extra data: line N column 2 (char M)` on the leftover bytes (confirmed on
  oneDAL's `cpp/oneapi/dal.hpp`, which transitively includes
  `<sycl/sycl.hpp>`). `_build_clang_header_command` now appends
  `-fsycl-host-only` whenever `-fsycl` is present in `--gcc-options`/
  `--gcc-option` *and* the resolved compiler is specifically one of those
  Intel aliases (new `dumper_clang._is_intel_sycl_driver`), without an
  explicit `-fsycl-host-only`/`-fsycl-device-only` already pinning a single
  pass — collapsing the compile back to the single host-side AST that
  actually matches what links into the scanned `.so` (the device pass's
  SPIR-V kernel code never does). The gate is deliberately narrower than
  "any clang-family binary": stock upstream clang also accepts a bare
  `-fsycl` and parses it fine as one pass, but does not recognize either
  `-fsycl-host-only` or `-fsycl-device-only` and hard-rejects them with
  "unknown argument" — appending the flag unconditionally would have turned
  a working `--gcc-path clang` + `-fsycl` parse into a guaranteed failure
  (Codex review, PR #643, caught after an initial version of this fix keyed
  only on `-fsycl`'s presence). The gate also tracks the *effective* SYCL
  state rather than a bare membership check: the clang driver applies
  `-fsycl`/`-fno-sycl` last-flag-wins like any other toggle (confirmed with
  `clang++ -fsycl -fno-sycl -###`, which emits one ordinary host `-cc1`, no
  device pass), so `--gcc-options "-fsycl -fno-sycl"` has SYCL disabled
  overall and must not get `-fsycl-host-only` appended either — that would
  tack a SYCL-only selector onto a non-SYCL compile (Codex review, PR #643,
  round 5). Also improved `_parse_clang_ast_result`'s
  JSON error message to name this class of cause (multiple `-cc1` passes
  from one compile, e.g. an unpinned `-fsycl` on an Intel driver, or an
  OpenMP/CUDA offload target flag) instead of a bare byte-offset, for any
  future flag combination that hits the same multi-document shape.
- **`_is_gnu_compiler_resource_dir` (follow-up to the `normpath` fix above):
  a symlinked path component made lexical `..` collapsing wrong** — a
  symlink followed by `..` resolves relative to the symlink's *target*
  directory at the OS level, not the symlink's own location, so purely
  lexical normalization can misjudge which directory an unresolved
  `../`-bearing search-dir path actually denotes (Codex review, PR #643).
  Classifying only `os.path.realpath(d)` fixes that but reintroduces the
  opposite failure — a *terminal* symlink at GCC's own canonical resource
  path (e.g. `.../lib/gcc/<triple>/<ver>/include` symlinked to storage
  outside any `lib/gcc` hierarchy) loses the lexical evidence that this is
  GCC's resource dir, so `_probe_gnu_system_includes` now rejects a
  directory when *either* the raw reported string or its
  `os.path.realpath(d)` classifies as a resource dir — safe since the
  directory's existence (`Path(d).is_dir()`) is already confirmed by that
  point — while `_is_gnu_compiler_resource_dir` itself keeps its
  lexical-only, pure/string-testable contract for callers that don't have
  (or need) a real path on disk.
