### Added

- **`.abicheck.yml` is now also recognized under `.github/`, in addition to
  a project's root.** `compare`'s project-config auto-discovery (walking up
  from the current directory) and `dump --sources`/`--build-info`'s
  source-tree-root discovery both now check, in each directory, three
  locations in order: `.abicheck.yml` (unchanged), `.github/.abicheck.yml`,
  and `.github/abicheck/.abicheck.yml` — the first one present wins, so a
  project already using `.github/` for workflows/`CODEOWNERS` can keep its
  abicheck config alongside them (or in its own `.github/abicheck/`
  subdirectory) without cluttering the repository root. The file's content,
  schema, and strict-loading rules are unchanged regardless of which of the
  three locations it's found at; an explicit `--config <path>` still
  overrides discovery entirely. A relative `compile.include_dirs` entry
  keeps resolving against the *project root* (the directory containing
  `.github/`), never against `.github/` or `.github/abicheck/` itself. See
  `docs/reference/config-file.md` § File discovery.
