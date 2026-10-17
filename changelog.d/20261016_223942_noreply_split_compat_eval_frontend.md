<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Compatibility-evaluation inputs split out of the resolver module** — `compatibility_evaluation_frontend.py` (1,859 lines, over the 800-line cap) gave three cohesive responsibilities to `workflows/`: the already-read suppression and symbol-list sources with `collect_force_public_symbols` (`compare_input_sources`), the built-in policy-base and severity-preset identities (`evaluation_identities`), and the `compare`-kwargs and typed-request adapters (`compare_input_adapters`). They live under `workflows/` because `workflows` callers import them and `frontends/` may not be imported from `workflows`. The public Python API of the resolver is unchanged apart from these names moving to their new modules, and resolution behaviour is unchanged.
