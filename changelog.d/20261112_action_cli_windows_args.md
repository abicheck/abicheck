### Fixed

- The GitHub Action's helper CLI (`python -m abicheck.frontends.action.cli`)
  no longer lets Click glob-expand its arguments on Windows runners, so a
  `base-equivalent-paths` value such as `include/**` reaches it verbatim
  instead of as a list of matching files.
