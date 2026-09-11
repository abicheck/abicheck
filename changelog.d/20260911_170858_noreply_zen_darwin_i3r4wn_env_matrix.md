<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Reject non-string `deployment.runtime_floors` keys.** A `.abicheck.yml`
  `runtime_floors:` mapping with a non-string key (e.g. an unquoted YAML int
  like `123: "2.28"`, or a bool key like `true: "2.28"`) used to be silently
  coerced by `str(key).upper()` into a spelling (`"123"`/`"TRUE"`) no
  named-prefix detector (`GLIBC`/`MUSLLINUX`/`WHEEL_ARCH`/...) recognizes, so
  the declared floor was accepted but permanently inert. `EnvironmentMatrix`'s
  `runtime_floors` parser now validates each key is a string before
  normalizing it, raising the same strict-config error already raised for
  other malformed `runtime_floors` shapes.

