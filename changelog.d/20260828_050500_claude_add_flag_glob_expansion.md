### Security

- **The composite GitHub Action's `add_flag`/`add_sided_flag` helpers no
  longer glob-expand a single-line caller input.** `action/run.sh`'s legacy
  single-line splitting path (`for item in $value`, used for e.g.
  `header`/`include`/`search-path`/`build-target`/`crosscheck`/`used-by`/
  `required-symbol`) was unquoted, so it performed pathname expansion as
  well as word-splitting: a value of exactly `*` (or one that happened to
  match a real filename in the runner's own checkout) silently expanded to
  every matching file in the current working directory instead of being
  passed through as the literal string — confirmed by direct execution.
  `add_flag_shlex_split`'s own fallback already refused this exact class of
  value for that reason; `add_flag`/`add_sided_flag` had no equivalent
  guard. Fixed by disabling pathname expansion (`set -f`) for the legacy
  split, restoring the prior glob setting afterward; legacy whitespace
  splitting and the newline-separated form are otherwise unchanged.
