### Fixed

- **`docs/reference/cli-reference.md` no longer goes stale on a Click
  upgrade.** Click 8.5 represents a boolean flag's *implicit* default with
  its internal `UNSET` sentinel where earlier versions stored a literal
  `False`, and `scripts/gen_cli_reference.py` suppressed that sentinel as
  "no default given" — so every flag's documented default flipped from
  `` `False` `` to `—` purely by which Click generated the file, failing the
  committed-reference sync check on every unit-test lane. The generator now
  resolves a boolean flag's unset default back to the value Click itself
  passes the command; a non-boolean `flag_value=` option, whose implicit
  default really is `None`, still renders `—`.
