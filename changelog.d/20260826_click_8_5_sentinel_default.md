### Fixed

- **`docs/reference/cli-reference.md`'s generator (`scripts/gen_cli_reference.py`)
  mis-rendered every boolean flag's default as "—" under Click 8.5+.**
  Click 8.5 widened its internal "no default was explicitly given"
  sentinel to cover *every* `is_flag=True` option with no explicit
  override, not just genuinely optionless ones (Click 8.4.2 reported a
  concrete `False` for a plain flag's `.default`; Click 8.5.0 reports the
  same internal sentinel Click already used for a truly-unset option). An
  omitted flag still resolves to `False` at parse time either way, so
  `_option_row` now treats a sentinel default as `False` specifically for
  single-value `is_bool_flag` options (not `is_flag`, which Click also
  sets for a non-boolean `flag_value=` option that genuinely has no
  default; and excluding `multiple=True`, which resolves to `()` rather
  than `False`) -- preserving the reference's existing distinction
  between "a disabled-by-default flag" and "no default at all" instead of
  collapsing every boolean flag into the latter the moment Click 8.5 is
  installed. A non-boolean `flag_value=` option and a `multiple=True`
  option both still correctly render "—".
