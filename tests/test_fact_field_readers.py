# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit-test mirror of the ``fact-field-readers`` AI-readiness check
(``scripts/fact_field_readers.py``, registered by
``scripts/check_ai_readiness.py``) — ADR-063 Phase 0's "widened, non-glob
AI-readiness check" (``docs/contribute/plans/one-semantic-pipeline.md``).

The check ERRORs if a module outside ``EXEMPT_MODULES`` reads one of
``RecordType.bases``/``virtual_bases``/``vtable`` or ``Param.is_va_list``
directly (an ``ast.Load``), without the site being a previously reviewed
``KNOWN_UNMIGRATED_READERS`` baseline entry — the same allowlist-and-shrink
design ``ENGINE_CLI_BOUNDARY_ALLOWLIST``/``IMPORT_CYCLE_ALLOWLIST`` already
use. This file pins that the real repository has no *unlisted* violation
and that the detection logic itself actually catches a direct read.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_ai_readiness import Findings  # noqa: E402
from scripts.fact_field_readers import (  # noqa: E402
    EXEMPT_MODULES,
    FACT_BRIDGED_ATTRS,
    KNOWN_UNMIGRATED_READERS,
    check_fact_field_readers,
    unmigrated_fact_reader_sites,
)


def test_no_unlisted_violation_in_real_repo() -> None:
    """The real repository has zero *unlisted* unmigrated readers.

    A pre-existing one must be named in ``KNOWN_UNMIGRATED_READERS``
    (reviewed, not silently accumulating) — this pins that the check itself
    is clean against the actual tree, not just against a synthetic fixture.
    """
    findings = Findings()
    check_fact_field_readers(findings)
    errors = [m for c, m in findings.errors if c == "fact-field-readers"]
    assert errors == [], "Unlisted Fact-bridged-field readers:\n" + "\n".join(errors)


def test_baseline_entries_are_real_sites() -> None:
    """Every ``KNOWN_UNMIGRATED_READERS`` entry must still name a real,
    currently-existing read — an entry that no longer matches anything is
    dead weight the baseline should have shrunk (the reader was migrated,
    or the code moved/was deleted), not a permanent grandfather clause."""
    import scripts.fact_field_readers as gate

    seen: set[str] = set()
    for path in sorted(gate.PKG.rglob("*.py")):
        rel = gate._rel(path)
        if rel in EXEMPT_MODULES:
            continue
        try:
            tree = ast.parse(gate._read(path), filename=rel)
        except SyntaxError:
            continue
        seen.update(
            key for key, _lineno, _attr in unmigrated_fact_reader_sites(tree, rel)
        )
    stale = KNOWN_UNMIGRATED_READERS - seen
    assert stale == set(), f"Stale baseline entries (no longer a real read): {stale}"


def test_exempt_modules_are_real_files() -> None:
    """An ``EXEMPT_MODULES`` entry naming a file that no longer exists (a
    rename, a deletion) is a silent hole — the exemption would then apply
    to nothing, while a reviewer reading the set believes it still covers a
    real bridge/producer module."""
    for rel in EXEMPT_MODULES:
        assert (_REPO_ROOT / rel).is_file(), (
            f"EXEMPT_MODULES names a missing file: {rel}"
        )


class TestUnmigratedFactReaderSites:
    """Direct tests on the AST-walking primitive, independent of the real
    repository tree — pins the detection logic's own contract."""

    def test_detects_a_direct_read_of_each_bridged_attr(self) -> None:
        src = (
            "def f(rec, p):\n"
            "    a = rec.bases\n"
            "    b = rec.virtual_bases\n"
            "    c = rec.vtable\n"
            "    d = p.is_va_list\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py")
        attrs = {attr for _key, _lineno, attr in sites}
        assert attrs == FACT_BRIDGED_ATTRS

    def test_ignores_an_assignment_target(self) -> None:
        """A `Store` context (`rec.vtable = []`, the legacy-schema backfill
        shape `storage/fact_codec.py` uses) is writing the field, not
        reading it as if unambiguous — not the failure mode this check
        exists to catch."""
        src = "def f(rec):\n    rec.vtable = []\n"
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py") == []

    def test_ignores_an_unrelated_attribute_name(self) -> None:
        src = "def f(rec):\n    return rec.size_bits\n"
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py") == []

    def test_occurrence_numbering_is_stable_per_attr_per_file(self) -> None:
        """Two reads of the same attribute in one file get distinct,
        top-to-bottom-ordered occurrence numbers — the key format
        `ENGINE_CLI_BOUNDARY_ALLOWLIST` already established, so an
        unrelated edit elsewhere in the file (which changes line numbers
        but not which reads exist, in which order) doesn't silently
        invalidate the baseline."""
        src = "def f(rec):\n    a = rec.bases\n    b = rec.bases\n"
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _lineno, _attr in unmigrated_fact_reader_sites(tree, "x.py")
        ]
        assert keys == ["x.py::bases::1", "x.py::bases::2"]

    def test_two_different_attrs_on_the_same_line_each_get_their_own_key(self) -> None:
        src = "def f(rec):\n    return rec.bases, rec.vtable\n"
        tree = ast.parse(src, filename="x.py")
        keys = {
            key for key, _lineno, _attr in unmigrated_fact_reader_sites(tree, "x.py")
        }
        assert keys == {"x.py::bases::1", "x.py::vtable::1"}


def test_check_reports_a_new_unlisted_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a real, unlisted read in a throwaway `abicheck/`-shaped
    tree fails the gate — pins that the check function itself (not just the
    AST primitive) actually enforces the baseline, against a real file on
    disk rather than only an in-memory `ast.Module`."""
    import scripts.fact_field_readers as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "a_new_reader.py").write_text("def f(rec):\n    return rec.bases\n")

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)

    findings = Findings()
    check_fact_field_readers(findings)
    errors = [m for c, m in findings.errors if c == "fact-field-readers"]
    assert len(errors) == 1
    assert "a_new_reader.py:2" in errors[0]
    assert "bases" in errors[0]


def test_check_is_silent_for_a_baselined_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for the test above: the identical read, at the
    identical stable key, produces no finding once it's in the baseline —
    the gate must not fire on every known, reviewed site forever."""
    import scripts.fact_field_readers as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "a_new_reader.py").write_text("def f(rec):\n    return rec.bases\n")

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(
        gate,
        "KNOWN_UNMIGRATED_READERS",
        frozenset({"abicheck/a_new_reader.py::bases::1"}),
    )

    findings = Findings()
    check_fact_field_readers(findings)
    errors = [m for c, m in findings.errors if c == "fact-field-readers"]
    assert errors == []
