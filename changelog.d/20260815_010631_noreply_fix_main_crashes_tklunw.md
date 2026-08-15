### Fixed

- **`--gcc-options`/`--compiler-option` quoted values no longer break apart
  on Windows.** A shell-quoted value like `-DMSG="hello world"` used to
  split into malformed tokens (`-DMSG="hello`, `world"`) everywhere the
  forwarded compiler-flags string was tokenized on Windows, because
  `shlex.split(..., posix=os.name != "nt")` traded quote-collapsing for
  backslash-preserving depending on the host OS instead of keeping both.
  A new shared helper, `abicheck._compiler_options.split_gcc_options`, now
  handles a double-quoted value correctly on every platform (including one
  with an escaped embedded quote, like `-DMSG="say \"hi\""`), while on
  Windows specifically an *unquoted* backslash — including an ordinary
  path (`-IC:\mypath\include`), one ending in a trailing directory
  separator right before the next flag (`-IC:\sdk\ -DOK=1`), or a literal
  apostrophe in the path (`-IC:\Users\O'Brien\include`) — is left
  untouched rather than corrupted, except when it immediately precedes a
  double-quote character (`-DVERSION=\"1.2\"`), which it still escapes;
  POSIX platforms keep their exact prior, unmodified
  `shlex.split(text, posix=True)` behavior (including real POSIX escapes
  such as `-DVAR=\$HOME`). Fixes the same class of bug in
  `action/run.sh`'s `add_flag_shlex_split`, which now delegates to the
  real Python helper directly (via stdin, to also avoid Git Bash/MSYS's
  automatic argv path-conversion mangling a value containing a
  POSIX-style path segment) instead of carrying its own copy of the
  tokenizer, and no longer lets a stray carriage return survive into a
  forwarded flag on Windows (native `python.exe`'s `print()` writes CRLF
  line endings on that platform, which bash's `read` alone doesn't strip).

### Security

- **`action/run.sh`'s inline Python invocations no longer let a checked-out
  repository shadow the installed `abicheck` package.** `python -c`/`-m`
  insert the current working directory into `sys.path[0]`, so on a
  `pull_request`-triggered workflow — where the checkout is the PR
  author's own, untrusted code — a PR that added a same-named module (e.g.
  its own `abicheck/_compiler_options.py` or `abicheck/package.py`), or a
  top-level `sitecustomize.py`/`usercustomize.py` (auto-imported by
  Python's `site` module during interpreter *startup*, before any `-c`
  script body runs), could make one of these inline scripts import and
  execute that code instead of the real, pip-installed package — including
  via a `PYTHONPATH` entry the calling workflow set, resolved relative to
  the checkout. Every Python invocation in this script now runs from a
  freshly created, empty temporary directory with `PYTHONPATH` cleared for
  that one invocation — not just the ones importing `abicheck`, since the
  `sitecustomize.py` auto-import vector fires during interpreter startup
  regardless of what (if anything) the invoked script body itself imports
  — so the checkout is never on `sys.path`
  at any point during interpreter startup or after — closing both the
  direct-import and the `sitecustomize` vector at once, for a bare CWD
  entry, a `PYTHONPATH=.`-style entry, or a `PYTHONPATH=src`-style
  descendant entry alike — and the one `-m abicheck.cli_pr_comment`
  invocation was converted to the equivalent `-c`-based form so it could
  receive the same fix. Creating that temporary directory now fails the
  Action outright (rather than silently falling back to a shared,
  non-private one like `${TMPDIR:-/tmp}`) if it can't be created, since a
  shared fallback would reintroduce the same shadowing risk on a
  constrained or shared self-hosted runner; separately, the resolved
  Python interpreter is now checked once, up front — itself run under the
  same temporary-directory/cleared-`PYTHONPATH` isolation, so the check
  can't reopen the checkout-shadowing vector it exists to guard against —
  for whether it can actually import `abicheck` (a self-hosted runner can
  expose `pip`/`abicheck` from one Python environment while a bare
  `python3` on `PATH` resolves a different one). `--gcc-options`/
  `--compiler-option` now fails the Action loud, rather than silently
  falling back to plain whitespace splitting, whenever the value actually
  needs real quote/escape-aware parsing to interpret correctly (contains
  `"`, `'`, or `\`) and no working `abicheck`-capable interpreter is
  available, or whenever the value's quoting is malformed (e.g. an
  unbalanced quote) even when a working interpreter is available — both
  previously produced an apparently-successful comparison silently run
  under a different, wrong compile context instead of failing on the
  invalid configuration; the same fallback also now rejects a value
  containing a glob metacharacter (`*`, `?`, `[`), since `add_flag()`'s
  own unquoted splitting performs pathname *expansion* too, not just
  whitespace-splitting — a value like `-DPATTERN=*` could otherwise
  silently rewrite to whatever filenames exist in the analyzed checkout
  at the time it runs. A value with none of these special characters
  still falls back to plain whitespace splitting, since that's provably
  identical to real parsing for that shape. The temporary directory's
  cleanup is now also registered immediately after it's created (rather
  than left to the script's main cleanup trap, installed much later), so
  an early exit — argument validation, a no-baseline dry-run success —
  can no longer leave it behind, accumulating private temporary
  directories across repeated invocations on a persistent self-hosted
  runner. Separately, the baseline-set-archive temporary directory is now
  canonicalized to an absolute path immediately after creation: `mktemp
  -d` returns a path relative to `$TMPDIR` when that variable itself
  holds a relative value, and every path derived from it is passed into
  the same `$_PY_SAFE_DIR`-isolated invocations above, which would
  otherwise resolve a relative path against the wrong directory and
  misreport a perfectly valid archive as corrupt or missing. `_report_query`
  (the Job Summary's severity/coverage lookups) is anchored the same way:
  its own report-path argument — which, unlike every other path this
  isolation touches, can genuinely be a bare, relative user-supplied
  `output-file` value — is resolved against the working directory before
  the isolated invocation runs, not after.
- **The Windows-only compiler-flags tokenizer (`--gcc-options`/
  `--compiler-option`) now follows the standard Windows command-line
  backslash/quote parsing rule** (the same one `CommandLineToArgvW` and
  real MSVC-family command lines use) instead of a hand-rolled grammar
  with separate, ad hoc rules for inside vs. outside quotes. This closes
  the last two gaps in that hand-rolled grammar at once: a quoted Windows
  UNC path no longer loses its leading double backslash (`-I"\\server\share
  path\include"` used to collapse to a single, non-UNC backslash), and a
  quoted path ending in a directory separator right before the closing
  quote (`-I"C:\Program Files\SDK\\"`) no longer fails to close the quoted
  region and raise a spurious error — both now resolved correctly by the
  same backslash-run-parity rule real Windows tooling uses.
- **`action/run.sh`'s baseline-set temporary directory now fails the Action
  loud, instead of silently continuing with an empty path, if canonicalizing
  it fails** (`cd "$BASELINE_DIR" && pwd` — this script has no `set -e`, so
  an unlikely `cd` failure previously left `BASELINE_DIR` empty and every
  path derived from it resolved against the filesystem root instead of the
  intended temporary directory).
- **`has_explicit_cpp_std` no longer raises on a malformed `--gcc-options`
  value** (e.g. an unbalanced quote) — it now degrades the same way its
  sibling `explicit_language_standard` already does, instead of letting
  `split_gcc_options`'s `ValueError` escape and abort the dump.
- **`$_PY_BIN` (the resolved Python interpreter `action/run.sh` uses for
  every inline invocation) is now canonicalized to an absolute path
  immediately after resolution.** `command -v python3` can return a path
  relative to the current working directory when `PATH` itself contains a
  relative entry (a real, if unusual, self-hosted-runner configuration) —
  every inline invocation runs as `(cd "$_PY_SAFE_DIR" && ... "$_PY_BIN"
  ...)`, so an uncanonicalized relative `$_PY_BIN` would resolve against
  `$_PY_SAFE_DIR` instead of the directory it was actually found relative
  to, making a genuinely working, abicheck-capable interpreter falsely
  resolve as unusable (Codex review).
- **`_report_query`'s report-path anchoring (and the same-shaped `$_PY_BIN`
  canonicalization) now also recognize a Windows UNC path
  (`\\server\share\report.json`), a root-relative path (`\report.json`),
  and a drive-relative path (`C:report.json`, no separator after the drive
  letter — relative to that drive's own current directory, a distinct real
  Windows path form) as already qualified**, not just a POSIX absolute path
  or a drive-absolute path — the previous check matched none of these, so
  such a report path was wrongly rewritten with a `$PWD/` prefix, making a
  real report silently unreadable and the Job Summary/PR-comment severity
  lookups fall back to their generic "no report available" message instead
  of the report's actual verdict (Codex review).
- **The Windows-only path-qualification check (drive letter, UNC, root-
  relative) used by `$_PY_BIN` canonicalization and `_report_query`'s
  report-path anchoring is now gated on actually running on Windows
  (`$OSTYPE`), not applied unconditionally on every platform.** Extracted
  into one shared `_is_path_already_qualified()` helper instead of two
  duplicated `case` patterns. Applying the drive-letter/backslash forms
  unconditionally meant a genuine POSIX relative filename shaped like a
  Windows path (e.g. `a:baseline.json`, a single character then a literal
  `:`) was wrongly recognized as already-qualified and left un-anchored on
  Linux/macOS too, instead of being anchored to `$PWD` like any other
  relative path (Codex review).
- **`_user_define_flags` (the ADR-039 build-context collector's global
  `-D`/`-U` harvester, `cli_dump_helpers.py`) now tokenizes its
  `--gcc-options` string with the shared `split_gcc_options`, not a bare
  `shlex.split`.** On Windows, an unquoted path value (e.g.
  `-IC:\sdk\ -UKEEP`) would tokenize differently between this collector
  (POSIX-only `shlex.split`, corrupting the path/merging tokens) and the
  real header-AST parse (already routed through `split_gcc_options`) —
  letting the harvested define set silently diverge from what the actual
  parse saw, which could make the reconciler add back a field the real
  parse had already pruned (Codex review).
- Fixed a genuine Windows CI regression surfaced by this PR's own review
  cycle: `TestSplitGccOptionsPosix` (pinning `split_gcc_options`'s plain,
  unmodified POSIX `shlex.split` behavior) ran unguarded on `windows-latest`,
  where real `os.name` is `"nt"` — `split_gcc_options` correctly took the
  Windows branch there, so every assertion in that class failed against the
  wrong tokenizer. `os.name` is now forced to `"posix"` for the whole class,
  matching the established pattern already used by
  `TestSplitGccOptionsDispatch`. Separately, several `action/run.sh` test
  harnesses (`test_action_compile_context_parity.py`,
  `test_action_run_sh_legacy_aliases.py`, `test_action_run_sh_py_safe_path.py`)
  invoked bash with the whole extracted script embedded as an inline `-c`
  argument; on `windows-latest`, Python's `subprocess` reconstructs a single
  Windows command-line string via `list2cmdline` (MSVC/CRT quoting), while
  Git Bash's MSYS runtime re-derives its own argv from that string using a
  materially different convention — for a script this size, with many
  nested/embedded quotes, the two didn't always round-trip losslessly,
  producing a genuine bash *parse* error unrelated to the script's own
  (verified-valid) syntax. All three now write the script to a temp file and
  run `bash <path>` instead, matching the pattern already established by
  `test_action_run_sh_helpers.py`'s `_run_harness`.
