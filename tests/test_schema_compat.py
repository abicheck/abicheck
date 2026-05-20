"""Tests for snapshot schema backward/forward compatibility (3a-3d).

Golden fixtures in tests/fixtures/schema/ represent each schema version with
identical logical content. Tests verify:
- Every version loads successfully
- Self-comparison produces NO_CHANGE
- Cross-version comparison produces NO_CHANGE
- Reserialization always writes current schema version
"""
import json
import warnings
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.serialization import (
    SCHEMA_VERSION,
    snapshot_from_dict,
    snapshot_to_dict,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "schema"

# All fixture files, parameterized
SCHEMA_VERSIONS = [
    pytest.param("v1.json", id="v1-no-schema-version"),
    pytest.param("v2.json", id="v2"),
    pytest.param("v3.json", id="v3"),
    pytest.param("v4.json", id="v4-provenance"),
    pytest.param("v5.json", id="v5-build-mode"),
]


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# 3b. Parameterized load tests
# ---------------------------------------------------------------------------

class TestLoadAllVersions:
    @pytest.mark.parametrize("fixture", SCHEMA_VERSIONS)
    def test_loads_successfully(self, fixture):
        d = _load_fixture(fixture)
        snap = snapshot_from_dict(d)
        assert snap.library == "libcompat.so.1"
        assert snap.version == "1.0.0"
        assert len(snap.functions) == 2
        assert len(snap.types) == 1
        assert len(snap.enums) == 1

    @pytest.mark.parametrize("fixture", SCHEMA_VERSIONS)
    def test_self_compare_no_change(self, fixture):
        d = _load_fixture(fixture)
        snap = snapshot_from_dict(d)
        result = compare(snap, snap)
        assert result.verdict == Verdict.NO_CHANGE

    @pytest.mark.parametrize("fixture", SCHEMA_VERSIONS)
    def test_functions_correct(self, fixture):
        d = _load_fixture(fixture)
        snap = snapshot_from_dict(d)
        names = {f.name for f in snap.functions}
        assert names == {"compat_init", "compat_free"}

    @pytest.mark.parametrize("fixture", SCHEMA_VERSIONS)
    def test_types_correct(self, fixture):
        d = _load_fixture(fixture)
        snap = snapshot_from_dict(d)
        assert snap.types[0].name == "compat_config"
        assert len(snap.types[0].fields) == 2

    @pytest.mark.parametrize("fixture", SCHEMA_VERSIONS)
    def test_enums_correct(self, fixture):
        d = _load_fixture(fixture)
        snap = snapshot_from_dict(d)
        assert snap.enums[0].name == "compat_status"
        assert len(snap.enums[0].members) == 2


# ---------------------------------------------------------------------------
# 3c. Cross-version comparison tests
# ---------------------------------------------------------------------------

class TestCrossVersionCompare:
    def test_v1_vs_v3_no_change(self):
        """Same content in v1 and v3 format — compare must produce NO_CHANGE."""
        snap_v1 = snapshot_from_dict(_load_fixture("v1.json"))
        snap_v3 = snapshot_from_dict(_load_fixture("v3.json"))
        result = compare(snap_v1, snap_v3)
        assert result.verdict == Verdict.NO_CHANGE

    def test_v1_vs_v4_no_change(self):
        """v4 has extra provenance fields but same ABI content."""
        snap_v1 = snapshot_from_dict(_load_fixture("v1.json"))
        snap_v4 = snapshot_from_dict(_load_fixture("v4.json"))
        result = compare(snap_v1, snap_v4)
        assert result.verdict == Verdict.NO_CHANGE

    def test_v2_vs_v4_no_change(self):
        snap_v2 = snapshot_from_dict(_load_fixture("v2.json"))
        snap_v4 = snapshot_from_dict(_load_fixture("v4.json"))
        result = compare(snap_v2, snap_v4)
        assert result.verdict == Verdict.NO_CHANGE

    def test_v3_vs_v4_no_change(self):
        snap_v3 = snapshot_from_dict(_load_fixture("v3.json"))
        snap_v4 = snapshot_from_dict(_load_fixture("v4.json"))
        result = compare(snap_v3, snap_v4)
        assert result.verdict == Verdict.NO_CHANGE

    def test_v4_vs_v5_no_change(self):
        """v5 adds build_mode metadata but the ABI surface is identical."""
        snap_v4 = snapshot_from_dict(_load_fixture("v4.json"))
        snap_v5 = snapshot_from_dict(_load_fixture("v5.json"))
        result = compare(snap_v4, snap_v5)
        assert result.verdict == Verdict.NO_CHANGE

    def test_v1_vs_v5_no_change(self):
        """Cross-jump: v1 ↔ v5 must still compare equal on identical ABI."""
        snap_v1 = snapshot_from_dict(_load_fixture("v1.json"))
        snap_v5 = snapshot_from_dict(_load_fixture("v5.json"))
        result = compare(snap_v1, snap_v5)
        assert result.verdict == Verdict.NO_CHANGE


# ---------------------------------------------------------------------------
# 3d. Reserialization stability tests
# ---------------------------------------------------------------------------

class TestReserialization:
    @pytest.mark.parametrize("fixture", SCHEMA_VERSIONS)
    def test_reserialized_to_current_version(self, fixture):
        """Loading any version and re-serializing should produce current schema_version."""
        d = _load_fixture(fixture)
        snap = snapshot_from_dict(d)
        reserialized = snapshot_to_dict(snap)
        assert reserialized["schema_version"] == SCHEMA_VERSION

    def test_v1_roundtrip_no_data_loss(self):
        """Load v1 → serialize → reload: all ABI content preserved."""
        d = _load_fixture("v1.json")
        snap = snapshot_from_dict(d)
        reserialized = snapshot_to_dict(snap)
        snap2 = snapshot_from_dict(reserialized)
        assert len(snap2.functions) == len(snap.functions)
        assert len(snap2.types) == len(snap.types)
        assert len(snap2.enums) == len(snap.enums)
        assert snap2.library == snap.library
        assert snap2.version == snap.version

    def test_v4_provenance_preserved(self):
        """v4 provenance fields survive roundtrip."""
        d = _load_fixture("v4.json")
        snap = snapshot_from_dict(d)
        assert snap.git_commit == "abc1234"
        assert snap.git_tag == "v1.0.0"
        reserialized = snapshot_to_dict(snap)
        assert reserialized["git_commit"] == "abc1234"
        assert reserialized["git_tag"] == "v1.0.0"

    def test_v5_build_mode_preserved(self):
        """v5 build_mode field survives roundtrip with enums rehydrated."""
        from abicheck.build_mode import (
            BuildMode,
            CompilerFamily,
            CxxStandard,
            GlibcxxDualAbi,
            StdlibFamily,
        )

        d = _load_fixture("v5.json")
        snap = snapshot_from_dict(d)
        assert isinstance(snap.build_mode, BuildMode)
        assert snap.build_mode.compiler_family == CompilerFamily.GCC
        assert snap.build_mode.language_std == CxxStandard.CXX17
        assert snap.build_mode.stdlib == StdlibFamily.LIBSTDCXX
        assert snap.build_mode.glibcxx_dual_abi == GlibcxxDualAbi.CXX11
        assert snap.build_mode.provenance.compiler_version == "11.4.0"

        # Round-trip: serialize → reload → equal.
        reserialized = snapshot_to_dict(snap)
        bm = reserialized["build_mode"]
        assert bm["compiler_family"] == "gcc"
        assert bm["language_std"] == "c++17"
        snap2 = snapshot_from_dict(reserialized)
        assert snap2.build_mode == snap.build_mode

    def test_v4_loads_with_build_mode_none(self):
        """Older v4 snapshots that lack build_mode load as None — the
        loader must not assume the field is present. This is the
        backward-compat guarantee for the schema-v5 bump."""
        d = _load_fixture("v4.json")
        snap = snapshot_from_dict(d)
        assert snap.build_mode is None

    def test_malformed_build_mode_falls_back_to_none(self):
        """A malformed ``build_mode`` payload (e.g. a string instead of
        a dict, or a dict whose ``provenance`` is the wrong shape) must
        load as None rather than raising. Regression for CodeRabbit's
        review: previously ``prov_raw.get(...)`` would raise on a
        non-dict provenance."""
        d = _load_fixture("v5.json")

        # Case 1: build_mode itself is a non-dict.
        d_bad = dict(d)
        d_bad["build_mode"] = "garbage"
        snap = snapshot_from_dict(d_bad)
        assert snap.build_mode is None

        # Case 2: provenance is a non-dict.
        d_bad = dict(d)
        d_bad["build_mode"] = {
            "compiler_family": "gcc",
            "provenance": "not-a-dict",
        }
        snap = snapshot_from_dict(d_bad)
        assert snap.build_mode is None

        # Case 3: libcpp_abi_version is a non-int (must coerce to None,
        # not raise downstream when other code does arithmetic on it).
        d_bad = dict(d)
        d_bad["build_mode"] = {
            "compiler_family": "clang",
            "libcpp_abi_version": "not-a-number",
            "provenance": {},
        }
        snap = snapshot_from_dict(d_bad)
        assert snap.build_mode is not None
        assert snap.build_mode.libcpp_abi_version is None

    def test_future_version_warning(self):
        """Loading a snapshot with schema_version > current emits a warning."""
        d = _load_fixture("v4.json")
        d["schema_version"] = 999
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            snap = snapshot_from_dict(d)
            assert len(w) == 1
            assert "newer than this abicheck" in str(w[0].message)
        # But still loads successfully
        assert snap.library == "libcompat.so.1"
