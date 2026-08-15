<!-- Follow-ups to #770, found by review after that PR merged. -->

### Fixed

- **Runtime messages no longer name removed side-prefixed flags.** The
  `--depth source` + `--ast-frontend hybrid` rejection told the user to pass
  `--old-ast-frontend castxml`, and three warnings described the operand as an
  `--old-sources`/`--old-build-info` tree. `--ast-frontend`, `--sources` and
  `--build-info` are all side-aware now, so following any of those hints
  produced a second, unrelated unknown-option error; they name the live
  `old=`/`new=` spellings.

- **The Action's compare branch no longer injects a `--write` that loses.**
  Its internal `--write json=$PR_JSON` is added before `extra-args`, and Click
  honors the last occurrence, so a user's own `--write` left `$PR_JSON` empty
  and the PR-comment step reran the whole comparison purely to obtain JSON --
  doubling a potentially expensive analysis to produce the file that injection
  exists to avoid rerunning for. Compare now applies the same
  `_extra_args_has_write_flag` guard the scan branch already had.
