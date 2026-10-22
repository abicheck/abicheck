### Changed

- **Compare's config fold, collect-mode fold and manifest preflight moved behind `workflows/compare`** — the typed configuration resolution, the `--depth` collect fold, the compile-context frontend split and the dump/pack manifest preflight now live in the workflow layer. The CLI only renders their typed refusals as usage errors and parses the flags into their inputs. Exit codes, messages and output are unchanged.
