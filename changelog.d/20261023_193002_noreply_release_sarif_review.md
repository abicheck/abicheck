<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **A directory/package `compare` now renders `sarif` and `review`.** `-o sarif=...` writes one SARIF run per release library, each result stamped with `properties.releaseMember` so a code-scanning consumer can place it in the release; `-o review=...` writes a release digest (verdict, exit code, per-library counts, and each library's breaking findings) rendered from the release JSON document. The verdict and exit code are unchanged by either format. The Action's pre-install check and `compare`'s `-o` help now read the same format set (`RELEASE_FORMATS`) as the CLI, from one generated file (`action/cli-surface.txt`).
