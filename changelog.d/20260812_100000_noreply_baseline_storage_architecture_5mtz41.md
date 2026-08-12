### Security

- **`actions/stage-baseline` no longer writes an unvalidated value to
  `GITHUB_OUTPUT`** — the resolved asset name is now rejected if it
  contains a newline, carriage return, or path separator, closing a
  GitHub Actions output-injection vector reachable when `profile`/
  `asset-name-template` are influenced by external metadata.
