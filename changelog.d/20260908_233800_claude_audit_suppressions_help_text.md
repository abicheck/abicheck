<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- `compare --audit-suppressions`'s help text and the generated CLI reference
  still documented the flag as "Requires --suppress" and claimed it always
  adds an audit section — stale since a prior fix made it a harmless no-op
  when no `--suppress` file is given. Help text and `docs/reference/cli-reference.md`
  now describe the no-op behavior.
