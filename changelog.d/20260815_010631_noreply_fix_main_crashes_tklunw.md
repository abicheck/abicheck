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
  the checkout. Every inline script that imports an `abicheck` module now
  runs from a freshly created, empty temporary directory with `PYTHONPATH`
  cleared for that one invocation, so the checkout is never on `sys.path`
  at any point during interpreter startup or after — closing both the
  direct-import and the `sitecustomize` vector at once, for a bare CWD
  entry, a `PYTHONPATH=.`-style entry, or a `PYTHONPATH=src`-style
  descendant entry alike — and the one `-m abicheck.cli_pr_comment`
  invocation was converted to the equivalent `-c`-based form so it could
  receive the same fix.
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
