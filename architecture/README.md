# Repository architecture contract

`module-boundaries.json` is the machine-readable source of truth for:

- target package responsibilities and allowed dependency direction;
- new-file and legacy-file growth thresholds;
- frozen top-level overflow-module prefixes;
- the seed inventory that guides the staged migration.

Human rationale, migration order, and acceptance criteria live in
[`docs/contribute/plans/module-boundaries-and-file-health.md`](../docs/contribute/plans/module-boundaries-and-file-health.md).
Package-local agent routing lives in [`abicheck/AGENTS.md`](../abicheck/AGENTS.md).

Run the canonical focused verification from the repository root:

```bash
python scripts/verify.py --profile pr \
  --only module-architecture-tests,module-architecture
```

The architecture step compares against `MODULE_ARCHITECTURE_BASE_REF` when CI
provides it, and otherwise against `origin/main`. Use
`python scripts/module_architecture.py --base-ref <ref> --format json` only for
focused diagnostics.

The JSON file is a contract, not a generated snapshot. Change it only with an
architecture rationale and matching tests for `scripts/module_architecture.py`.
