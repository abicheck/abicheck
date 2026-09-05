### Removed

- **`--btf`/`--ctf`/`--dwarf` legacy debug-format flags on `compare` and
  `dump`** — deleted outright (they were already hidden, no-op-superseded
  duplicates of `--debug-format {btf,ctf,dwarf}`, per CLAUDE.md's
  no-deprecation-window policy). Passing any of the three now exits `64`
  with `No such option`; `--debug-format` is the only selector left.
