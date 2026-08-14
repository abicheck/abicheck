### Removed

- **`--gcc-options` is removed (CLI audit PR 5/5).** The whitespace-split,
  single-string form is superseded by the repeatable, verbatim
  `--compiler-option` (added in PR 2/5); pass a flag whose value contains
  spaces as two `--compiler-option` tokens instead of one whitespace-split
  string. The composite GitHub Action's own `gcc-options` input is
  unaffected — it now maps internally to `--compiler-option` (word-splitting
  a single-line value into separate tokens, same as before; a multi-line
  value keeps each line as one verbatim flag, spaces included).
