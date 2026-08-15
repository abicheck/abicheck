### Fixed

- **`--gcc-options`/`--compiler-option` quoted values no longer break apart
  on Windows.** A shell-quoted value like `-DMSG="hello world"` used to
  split into malformed tokens (`-DMSG="hello`, `world"`) everywhere the
  forwarded compiler-flags string was tokenized on Windows, because
  `shlex.split(..., posix=os.name != "nt")` traded quote-collapsing for
  backslash-preserving depending on the host OS instead of keeping both.
  A new shared helper, `abicheck._compiler_options.split_gcc_options`,
  always applies POSIX-style quote/whitespace splitting with escaping
  disabled, so a quoted value stays one token *and* a literal Windows path
  (`-IC:\mypath\include`) keeps its backslashes, on every platform. Fixes
  the same class of bug in `action/run.sh`'s `add_flag_shlex_split`, which
  now mirrors the identical fix in its inline Python.
