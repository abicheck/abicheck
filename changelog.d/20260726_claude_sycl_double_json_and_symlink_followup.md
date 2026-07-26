<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--ast-frontend clang` + `-fsycl` produced two concatenated JSON documents
  on stdout, breaking `json.load()`** (`abicheck/dumper.py`,
  `abicheck/dumper_clang_errors.py`): a bare `-fsycl` makes a SYCL-capable
  driver (Intel's `icpx`/`dpcpp`, or upstream clang's own SYCL support) run
  *two* separate `-cc1` passes for one compile — a SYCL device-side pass
  (`-fsycl-is-device`) and a SYCL host-side pass (`-fsycl-is-host`) — each
  with its own `-Xclang -ast-dump=json`, both writing a complete JSON
  document to the same stdout stream abicheck captures, back-to-back with no
  separator. `json.load()` parsed only the first document and raised
  `Extra data: line N column 2 (char M)` on the leftover bytes (confirmed on
  oneDAL's `cpp/oneapi/dal.hpp`, which transitively includes
  `<sycl/sycl.hpp>`). `_build_clang_header_command` now appends
  `-fsycl-host-only` whenever `-fsycl` is present in `--gcc-options`/
  `--gcc-option` without an explicit `-fsycl-host-only`/`-fsycl-device-only`
  already pinning a single pass — collapsing the compile back to the
  single host-side AST that actually matches what links into the scanned
  `.so` (the device pass's SPIR-V kernel code never does). Also improved
  `_parse_clang_ast_result`'s JSON error message to name this class of cause
  (multiple `-cc1` passes from one compile, e.g. an unpinned `-fsycl` or an
  OpenMP/CUDA offload target flag) instead of a bare byte-offset, for any
  future flag combination that hits the same multi-document shape.
- **`_is_gnu_compiler_resource_dir` (follow-up to the `normpath` fix above):
  a symlinked path component made lexical `..` collapsing wrong** — a
  symlink followed by `..` resolves relative to the symlink's *target*
  directory at the OS level, not the symlink's own location, so purely
  lexical normalization can misjudge which directory an unresolved
  `../`-bearing search-dir path actually denotes (Codex review, PR #643).
  `_probe_gnu_system_includes` now classifies `os.path.realpath(d)` instead
  of the raw reported string — safe since the directory's existence
  (`Path(d).is_dir()`) is already confirmed by that point — while
  `_is_gnu_compiler_resource_dir` itself keeps its lexical-only, pure/
  string-testable contract for callers that don't have (or need) a real
  path on disk.
