### Fixed

- **The GitHub Action no longer globs `extra-args` against the workspace.** `extra-args: '*'` (or any `?`/`[...]` token) used to be expanded into the checkout's matching file names, so a file named `--format=json` could reach the CLI as a real flag. `action/run.sh` and the `check-target` overlay step now word-split `extra-args` as before, with pathname expansion off, and pass glob characters through literally.
