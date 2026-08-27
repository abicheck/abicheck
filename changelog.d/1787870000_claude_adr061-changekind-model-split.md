### Changed

- **ADR-061 D9 model-vs-policy split**: `ChangeKind`/`HasKind` moved from
  `checker_policy.py` to `abicheck/model/change_catalog/kinds.py`, the
  co-prerequisite an earlier investigation identified for building a
  model-owned `PolicyFile`/`ReclassifyRule` protocol facade. `checker_policy.py`
  re-exports both names unchanged, so this is not a public API change --
  every existing `from abicheck.checker_policy import ChangeKind` import
  keeps working, and the 397 members are verified byte-identical in name,
  value, and declaration order to the class they replace. Internal only:
  the enum is now assembled at runtime via the functional `Enum()` API from
  three sibling data files (kept under this repository's 800-line
  production file-size cap), with a generated mypy stub
  (`kinds.pyi`, `scripts/gen_changekind_stub.py`) preserving full static
  type checking for every `ChangeKind.FOO` access.
