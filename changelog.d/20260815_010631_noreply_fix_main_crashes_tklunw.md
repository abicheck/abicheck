### Fixed

- **`--gcc-options`/`--compiler-option` quoted values no longer break apart
  on Windows.** A shell-quoted value like `-DMSG="hello world"` used to
  split into malformed tokens (`-DMSG="hello`, `world"`) everywhere the
  forwarded compiler-flags string was tokenized on Windows, because
  `shlex.split(..., posix=os.name != "nt")` traded quote-collapsing for
  backslash-preserving depending on the host OS instead of keeping both.
  A new shared helper, `abicheck._compiler_options.split_gcc_options`, now
  handles a quoted value correctly on every platform (including one with
  an escaped embedded quote, like `-DMSG="say \"hi\""`, and real POSIX
  escapes such as `-DVAR=\$HOME`), while on Windows specifically an
  *unquoted* backslash — including an ordinary path (`-IC:\mypath\include`)
  — is left untouched rather than corrupted, except when it immediately
  precedes a quote character or whitespace (`-DVERSION=\"1.2\"`,
  `-DMSG=hello\ world`), which it still escapes; POSIX platforms keep
  their exact prior, unmodified `shlex.split(text, posix=True)` behavior.
  Fixes the same class of bug in `action/run.sh`'s `add_flag_shlex_split`,
  which now delegates to the real Python helper directly (via stdin, to
  also avoid Git Bash/MSYS's automatic argv path-conversion mangling a
  value containing a POSIX-style path segment) instead of carrying its own
  copy of the tokenizer.
