### Fixed

- **`--gcc-options`/`--compiler-option` quoted values no longer break apart
  on Windows, and an unquoted Windows path's backslashes are preserved.**
  A shell-quoted value like `-DMSG="hello world"` used to split into
  malformed tokens (`-DMSG="hello`, `world"`) everywhere the forwarded
  compiler-flags string was tokenized on Windows, because
  `shlex.split(..., posix=os.name != "nt")` traded quote-collapsing for
  backslash-preserving depending on the host OS instead of keeping both.
  A new shared helper, `abicheck._compiler_options.split_gcc_options`,
  now handles both correctly on every platform: a quoted value
  (`-DMSG="hello world"`, including one with an escaped embedded quote
  like `-DMSG="say \"hi\""`) always collapses to one token following real
  POSIX quoting rules, while an *unquoted* backslash — including an
  ordinary Windows path (`-IC:\mypath\include`) — is left untouched rather
  than consumed as an escape character, except when it immediately
  precedes a quote character or whitespace (`-DVERSION=\"1.2\"`,
  `-DMSG=hello\ world`), which it still escapes. Fixes the same class of
  bug in `action/run.sh`'s `add_flag_shlex_split`, which now delegates to
  the real Python helper directly instead of carrying its own copy of the
  tokenizer.
