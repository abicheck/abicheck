"""``AbiSnapshot``'s type index is keyed on qualified identity (H4).

Header backends store a record's bare spelling in ``RecordType.name`` and its
scope in ``qualified_name``. The type index used to key on ``name``, so
``algo_a::Parameter`` and ``algo_b::Parameter`` collided: a misleading
"Duplicate type names skipped (first-wins)" warning, and ``type_by_name``
answering with whichever record came first.

Oracle: each fixture record's *declared* scope (the namespace list the test
writes), never the index's own key function.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.entities import RecordType, TypeField

_INDEX_LOGGER = "abicheck.model.snapshot_index"

# (declared scope path, bare name) -- one short name, five distinct scopes:
# three sibling namespaces, a nested namespace and a class-scoped nested type.
_SCOPES: tuple[tuple[str, ...], ...] = (
    ("algo_a",),
    ("algo_b",),
    ("algo_c",),
    ("algo_d", "inner"),
    ("Holder",),
)


def _qualified(scope: tuple[str, ...], bare: str) -> str:
    return "::".join((*scope, bare))


def _record(scope: tuple[str, ...], bare: str, field: str) -> RecordType:
    # Shaped like the castxml extractor's output: bare ``name``, scope in
    # ``qualified_name``.
    return RecordType(
        name=bare,
        kind="struct",
        size_bits=32,
        fields=[TypeField(name=field, type="int", offset_bits=0)],
        qualified_name=_qualified(scope, bare),
    )


def _snapshot(types: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(library="libns.so", version="1", types=types)


def _index_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _INDEX_LOGGER and r.levelno >= logging.WARNING
    ]


class TestPureModel:
    @pytest.mark.parametrize("n_scopes", range(2, len(_SCOPES) + 1))
    def test_same_bare_name_in_distinct_scopes_never_warns(
        self, n_scopes: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        snap = _snapshot(
            [_record(s, "Parameter", f"f{i}") for i, s in enumerate(_SCOPES[:n_scopes])]
        )
        with caplog.at_level(logging.WARNING, logger=_INDEX_LOGGER):
            snap.index()
        assert _index_warnings(caplog) == []

    @pytest.mark.parametrize("scope_idx", range(len(_SCOPES)))
    def test_qualified_lookup_resolves_the_declared_record(
        self, scope_idx: int
    ) -> None:
        records = [_record(s, "Parameter", f"f{i}") for i, s in enumerate(_SCOPES)]
        snap = _snapshot(records)
        hit = snap.type_by_name(_qualified(_SCOPES[scope_idx], "Parameter"))
        assert hit is not None
        assert hit.fields[0].name == f"f{scope_idx}"

    def test_ambiguous_bare_lookup_misses_instead_of_guessing(self) -> None:
        snap = _snapshot([_record(s, "Parameter", "x") for s in _SCOPES[:3]])
        assert snap.type_by_name("Parameter") is None

    def test_unambiguous_bare_lookup_still_resolves(self) -> None:
        snap = _snapshot(
            [_record(("algo_a",), "Parameter", "a"), _record(("algo_b",), "Other", "b")]
        )
        hit = snap.type_by_name("Parameter")
        assert hit is not None and hit.qualified_name == "algo_a::Parameter"

    def test_global_record_keeps_its_bare_key_next_to_a_namespaced_twin(self) -> None:
        glob = RecordType(name="Parameter", kind="struct", fields=[])
        snap = _snapshot([_record(("algo_a",), "Parameter", "a"), glob])
        # The global type's qualified spelling *is* "Parameter".
        assert snap.type_by_name("Parameter") is glob
        assert snap.type_by_name("algo_a::Parameter") is not glob

    def test_unqualified_dwarf_style_records_key_on_name(self) -> None:
        # DWARF stores the qualified spelling in ``name`` and no qualified_name.
        recs = [RecordType(name=f"{ns}::Parameter", kind="struct") for ns in "abc"]
        snap = _snapshot(recs)
        for rec in recs:
            assert snap.type_by_name(rec.name) is rec

    def test_true_duplicate_qualified_name_still_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        first = _record(("algo_a",), "Parameter", "first")
        snap = _snapshot([first, _record(("algo_a",), "Parameter", "second")])
        with caplog.at_level(logging.WARNING, logger=_INDEX_LOGGER):
            snap.index()
        msgs = _index_warnings(caplog)
        assert len(msgs) == 1 and "algo_a::Parameter" in msgs[0]
        assert snap.type_by_name("algo_a::Parameter") is first


# ── castxml end-to-end ──────────────────────────────────────────────────────

_HEADER = """
namespace algo_a {{ struct Parameter {{ int a; }}; int fa(Parameter*); }}
namespace algo_b {{ struct Parameter {{ int b; {extra} }}; int fb(Parameter*); }}
namespace algo_c {{ struct Parameter {{ int c; }}; int fc(Parameter*); }}
namespace algo_d {{ namespace inner {{ struct Parameter {{ int d; }}; }}
                    int fd(inner::Parameter*); }}
struct Holder {{ struct Parameter {{ int h; }}; Parameter p; }};
int fh(Holder*);
"""

_SOURCE = """
#include "ns.h"
int algo_a::fa(Parameter* p) { return p->a; }
int algo_b::fb(Parameter* p) { return p->b; }
int algo_c::fc(Parameter* p) { return p->c; }
int algo_d::fd(inner::Parameter* p) { return p->d; }
int fh(Holder* h) { return h->p.h; }
"""


def _build(tmp: Path, extra: str) -> tuple[Path, Path]:
    tmp.mkdir()
    hdr = tmp / "ns.h"
    hdr.write_text(textwrap.dedent(_HEADER.format(extra=extra)), encoding="utf-8")
    (tmp / "ns.cpp").write_text(_SOURCE, encoding="utf-8")
    lib = tmp / "libns.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(lib), str(tmp / "ns.cpp")],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return lib, hdr


@pytest.mark.integration
def test_castxml_namespace_collision_end_to_end(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    if not (shutil.which("castxml") and shutil.which("g++")):
        pytest.skip("castxml and g++ required")
    from abicheck.checker import compare
    from abicheck.dumper import dump

    old_lib, old_hdr = _build(tmp_path / "v1", "")
    new_lib, new_hdr = _build(tmp_path / "v2", "int added;")
    with caplog.at_level(logging.WARNING, logger=_INDEX_LOGGER):
        old = dump(old_lib, headers=[old_hdr], version="1", compiler="c++")
        new = dump(new_lib, headers=[new_hdr], version="2", compiler="c++")
        # Extractor shape this fix relies on: bare name, scope elsewhere.
        params = [t for t in old.declarations.types if t.name == "Parameter"]
        assert {t.qualified_name for t in params} == {
            _qualified(s, "Parameter") for s in _SCOPES
        }
        old.index()
        new.index()
        result = compare(old, new)
    assert _index_warnings(caplog) == []

    for scope in _SCOPES:
        rec = old.type_by_name(_qualified(scope, "Parameter"))
        assert rec is not None and rec.qualified_name == _qualified(scope, "Parameter")
    assert old.type_by_name("Parameter") is None  # ambiguous: no guess

    size_findings = [c for c in result.changes if "Parameter" in (c.symbol or "")]
    assert size_findings, "the algo_b::Parameter growth must be reported"
    assert {c.symbol for c in size_findings if "::" in (c.symbol or "")} <= {
        "algo_b::Parameter"
    }
    assert all(
        c.symbol == "algo_b::Parameter" or "algo_b" in (c.description or "")
        for c in size_findings
    ), [(c.kind, c.symbol) for c in size_findings]
