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
  its own `abicheck/_compiler_options.py` or `abicheck/package.py`) could
  make one of these inline scripts import and execute that code instead of
  the real, pip-installed package. Every inline script that imports an
  `abicheck` module now strips the CWD entry from `sys.path` before doing
  so — filtering by *resolved path* against the real current directory,
  not by matching the literal strings `""`/`"."`, since a caller with
  `PYTHONPATH=.` set already has Python resolve that `.` into the
  checkout's own absolute path before the filter ever runs — and the one
  `-m abicheck.cli_pr_comment` invocation was converted to the equivalent
  `-c`-based form so it could receive the same fix. Every one of these
  invocations also now passes `-S` (skip automatic `site` processing) so
  a checked-out PR's own top-level `sitecustomize.py`/`usercustomize.py`
  can no longer execute during interpreter *startup* — before the
  `sys.path`-filtering snippet above ever gets a chance to run a single
  line — with `site.main()` called manually right after the filter to
  restore normal site-packages access for the real, pip-installed package.
- **A quoted Windows UNC path forwarded via `--gcc-options`/
  `--compiler-option` no longer loses its leading double backslash.** The
  Windows-only tokenizer's double-quote handling followed real POSIX
  double-quote escaping unconditionally, which collapses `\\` to a single
  `\` — corrupting a value like `-I"\\server\share path\include"` into a
  single-backslash, non-UNC path the compiler can't resolve. Only a
  backslash immediately escaping the closing quote character is treated
  as an escape now; any other backslash inside quotes, including a
  doubled one, is left completely literal.
