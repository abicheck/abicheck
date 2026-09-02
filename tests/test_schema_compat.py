"""Tests for snapshot schema backward/forward compatibility (3a-3d).

Golden fixtures in tests/fixtures/schema/ represent each schema version with
identical logical content. Tests verify:
- Every version loads successfully
- Self-comparison produces NO_CHANGE
- Cross-version comparison produces NO_CHANGE
- Reserialization always writes current schema version
"""

import json
import re
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

    def test_malformed_build_mode_rejects_the_load(self):
        """A malformed ``build_mode`` payload (a string instead of a dict, a
        dict whose ``provenance`` is the wrong shape, or a non-int
        ``libcpp_abi_version``) must raise ``TypeError``, not silently
        degrade to ``None``/coerce (storage AGENTS.md invariant 6; Codex
        review, PR #974, fresh evidence) -- a corrupt ``build_mode`` reading
        as "predates this field" would let ``_effective_build_mode`` infer
        weaker facts from evidence that was never actually collected, and a
        string ``libcpp_abi_version`` coercing to int collapsed
        ``1``/``"1"`` onto one trusted value.

        Superseded the previous "falls back to None" contract this test
        pinned (CodeRabbit review): that fix's real point -- a non-dict
        ``provenance`` must not raise an opaque ``AttributeError`` from
        ``prov_raw.get(...)`` -- still holds here, just via a deliberate,
        typed ``TypeError`` instead of either the original opaque
        ``AttributeError`` or a silently-dropped field.
        """
        d = _load_fixture("v5.json")

        # Case 1: build_mode itself is a non-dict.
        d_bad = dict(d)
        d_bad["build_mode"] = "garbage"
        with pytest.raises(TypeError):
            snapshot_from_dict(d_bad)

        # Case 2: provenance is a non-dict -- must raise TypeError, not the
        # opaque AttributeError the original CodeRabbit fix guarded against.
        d_bad = dict(d)
        d_bad["build_mode"] = {
            "compiler_family": "gcc",
            "provenance": "not-a-dict",
        }
        with pytest.raises(TypeError):
            snapshot_from_dict(d_bad)

        # Case 3: libcpp_abi_version is a non-int.
        d_bad = dict(d)
        d_bad["build_mode"] = {
            "compiler_family": "clang",
            "libcpp_abi_version": "not-a-number",
            "provenance": {},
        }
        with pytest.raises(TypeError):
            snapshot_from_dict(d_bad)

    def test_future_version_hard_rejects(self):
        """Loading a snapshot with schema_version >=
        _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION raises
        IncompatibleSnapshotSchemaError rather than merely warning
        (ADR-050 D1) — a verdict-blocking field was introduced at that
        threshold, so an old reader must not silently continue past it."""
        from abicheck.errors import IncompatibleSnapshotSchemaError

        d = _load_fixture("v4.json")
        d["schema_version"] = 999
        with pytest.raises(IncompatibleSnapshotSchemaError):
            snapshot_from_dict(d)

    def test_sectioned_envelope_bump_hard_rejects_a_pre_phase_8_reader(
        self, monkeypatch
    ):
        """ADR-062/063 Phase 8 (redesign, Codex review, fresh evidence):
        snapshot_to_json() now writes storage.sectioned_document's envelope
        instead of a flat document -- a wire-format change, not just a new
        field. SCHEMA_VERSION was bumped specifically so a reader whose own
        SCHEMA_VERSION still names the pre-redesign value (41) hits this
        module's existing hard-rejection path instead of silently reading
        every top-level field of a real sectioned document as absent/empty
        (the sections are nested under "sections", which such a reader has
        no code to unwrap). Simulates that older reader by pinning this
        module's own SCHEMA_VERSION back to 41 -- everything else about the
        check (the >= _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION gate) is
        unchanged."""
        from abicheck import serialization
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.model import AbiSnapshot

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        doc = json.loads(serialization.snapshot_to_json(snap))
        assert serialization.is_sectioned_document(doc)
        assert doc["schema_version"] == serialization.SCHEMA_VERSION > 41

        monkeypatch.setattr(serialization, "SCHEMA_VERSION", 41)
        with pytest.raises(IncompatibleSnapshotSchemaError):
            serialization.snapshot_from_dict(doc)

    def test_older_version_warns_when_a_fact_is_degraded(self):
        """A snapshot older than SCHEMA_VERSION used to load with no signal
        at all -- the *_facts_reliable flags degraded silently, and CI
        (which reads a committed, once-generated baseline against whatever
        abicheck version its pin currently resolves to -- the "reader newer
        than snapshot" direction it always hits) never saw anything. Loading
        a legacy header-derived, clang-producer snapshot should now raise a
        loud UserWarning naming exactly which facts got degraded."""
        d = _load_fixture("v4.json")
        d["schema_version"] = 9
        d["from_headers"] = True
        d["ast_producer"] = "clang"
        with pytest.warns(UserWarning, match="clang_deprecation_facts_reliable"):
            snap = snapshot_from_dict(d)
        assert snap.clang_deprecation_facts_reliable is False

    def test_older_version_silent_when_nothing_is_degraded(self):
        """The opposite of the case above: an older snapshot whose producer
        isn't affected by any of the reliability flags shouldn't warn at all
        -- every CI baseline is *always* some number of versions behind, and
        warning regardless of whether that gap ever mattered would be
        exactly the noise this fix is not meant to introduce."""
        d = _load_fixture("v4.json")
        d["schema_version"] = SCHEMA_VERSION - 1
        d["from_headers"] = False
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            snapshot_from_dict(d)

    def test_older_version_silent_for_hybrid_producer_flags_no_detector_reads(self):
        """clang_va_list_facts_reliable / castxml_var_access_facts_reliable
        each have exactly one consumer, and each gates strictly on an EXACT
        producer match ("clang"-only, "castxml"-only respectively -- neither
        includes "hybrid", unlike most of the other flags). Their own value
        computation still marks both False for a "hybrid" producer (correct
        for the underlying fact's provenance), so naively listing every
        False flag would warn about "reduced coverage" a hybrid snapshot's
        regeneration could never actually restore, since no detector reads
        either flag for that producer regardless of schema version (Codex
        review, PR #720)."""
        d = _load_fixture("v4.json")
        d["schema_version"] = 22  # < both _MIN_SCHEMA_VERSION_FOR_CLANG_VA_LIST_FACTS
        # and _MIN_SCHEMA_VERSION_FOR_CASTXML_VAR_ACCESS_FACTS thresholds
        d["from_headers"] = True
        d["ast_producer"] = "hybrid"
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            snap = snapshot_from_dict(d)
        assert snap.clang_va_list_facts_reliable is False
        assert snap.castxml_var_access_facts_reliable is False

    def test_older_version_warns_for_clang_producer_va_list_flag(self):
        """The producer filter above must not over-filter: a genuine
        "clang" producer (not "hybrid") still gets the warning, since
        diff_symbols._diff_param_va_list DOES consult the flag for that
        producer."""
        d = _load_fixture("v4.json")
        d["schema_version"] = 22
        d["from_headers"] = True
        d["ast_producer"] = "clang"
        with pytest.warns(UserWarning, match="clang_va_list_facts_reliable"):
            snapshot_from_dict(d)

    def test_degraded_flag_still_warns_after_reserialization_to_current_schema(self):
        """snapshot_to_dict always re-stamps schema_version to the CURRENT
        SCHEMA_VERSION on save -- so a legacy snapshot that's loaded (which
        degrades a flag) and then simply re-saved reads as current-schema on
        its NEXT load, even though the underlying facts were never
        regenerated. Gating the warning purely on schema_version would make
        it silently disappear across exactly this round-trip (Codex review,
        PR #720) -- it must still fire, driven by the preserved explicit
        False marker rather than the version number."""
        d = _load_fixture("v4.json")
        d["schema_version"] = 9
        d["from_headers"] = True
        d["ast_producer"] = "clang"
        with warnings.catch_warnings(record=True) as first_load_warnings:
            warnings.simplefilter("always")
            snap = snapshot_from_dict(d)
        assert len(first_load_warnings) == 1

        resaved = snapshot_to_dict(snap)
        assert resaved["schema_version"] == SCHEMA_VERSION

        with pytest.warns(UserWarning, match="clang_deprecation_facts_reliable"):
            snap2 = snapshot_from_dict(resaved)
        assert snap2.clang_deprecation_facts_reliable is False

    def test_older_version_silent_for_inferred_header_flags_no_detector_reads(self):
        """Five of the seven flags' one real consumer requires CONFIRMED
        (non-inferred) header awareness before it ever reads the flag:
        clang_restrict/clang_va_list/castxml_var_access's detectors each
        exit through diff_symbols._both_header_aware, and clang_deprecation/
        clang_field_initializer's shared consumer
        (fact_provenance.fact_producer) opens with the identical
        ``from_headers and not from_headers_inferred`` check. A schema-v1
        snapshot with no explicit ``from_headers`` key gets it GUESSED true
        from a populated surface -- real, but not "confirmed" -- so none of
        these five detectors will ever consult their flag for it (Codex
        review, PR #720). header_cv_facts_reliable is the one exception
        exercised here: its consumers (variable/field cv checks) apply
        regardless of header confirmation, so it still warns."""
        d = _load_fixture("v1.json")
        d.pop("from_headers", None)
        d.pop("ast_producer", None)
        d["schema_version"] = 1
        with pytest.warns(UserWarning) as record:
            snap = snapshot_from_dict(d)
        assert snap.from_headers is True
        assert snap.from_headers_inferred is True
        assert len(record) == 1
        message = str(record[0].message)
        assert "header_cv_facts_reliable" in message
        for degraded_but_unconsulted in (
            "clang_deprecation_facts_reliable",
            "clang_field_initializer_facts_reliable",
            "clang_restrict_facts_reliable",
            "clang_va_list_facts_reliable",
            "castxml_var_access_facts_reliable",
        ):
            assert degraded_but_unconsulted not in message


# ---------------------------------------------------------------------------
# Exhaustive grid over the degraded-facts warning's (producer x header-
# confirmation) domain per flag (PR #720 retrospective). The three rounds of
# review that found the hybrid-producer, inferred-header, and round-trip
# bugs above each fixed exactly one (flag, producer, header-confirmed) cell
# at a time; nothing forced every OTHER cell in the same small grid to also
# be checked, so a fourth wrong cell could easily have shipped unnoticed.
# The grid is small enough (4 producers x 2 header-confirmation states x 7
# flags = 56 cells) to enumerate completely rather than keep adding one more
# hand-picked example per bug report — see
# ``abicheck/diff_default_value_reliability.py``'s module docstring for the
# same reasoning applied to the sibling fingerprint-reliability guards, and
# ``tests/test_fingerprint_reliability_properties.py`` for that exhaustive
# suite.
# ---------------------------------------------------------------------------

# Each flag's own DEGRADED-value rule at a schema version below every
# per-flag threshold (so only the producer term of each flag's real
# computation in snapshot_from_dict — see SCHEMA_VERSION's own version-
# history comments — still varies; the schema-threshold term is uniformly
# "not met" for every flag at schema_version=1).
_FLAG_DEGRADED_FOR_PRODUCER = {
    "header_cv_facts_reliable": lambda p: p != "clang",
    "clang_deprecation_facts_reliable": lambda p: p == "clang",
    "clang_field_initializer_facts_reliable": lambda p: p != "castxml",
    "clang_vtable_facts_reliable": lambda p: p == "clang",
    "clang_restrict_facts_reliable": lambda p: p != "castxml",
    "clang_va_list_facts_reliable": lambda p: p != "castxml",
    "castxml_var_access_facts_reliable": lambda p: p != "clang",
}

# Each flag's own CONSULTED rule -- does the one real detector that reads
# this flag even reach the point of checking it, for this (producer,
# header-confirmed) combination. Mirrors the table `snapshot_from_dict`
# builds (see its own extensive comment for the per-flag reasoning); this
# is the independently-maintained pin the exhaustive grid test below checks
# it against.
_FLAG_CONSULTED_FOR = {
    "header_cv_facts_reliable": lambda p, h: True,
    "clang_deprecation_facts_reliable": lambda p, h: h,
    "clang_field_initializer_facts_reliable": lambda p, h: h,
    "clang_vtable_facts_reliable": lambda p, h: True,
    "clang_restrict_facts_reliable": lambda p, h: h,
    "clang_va_list_facts_reliable": lambda p, h: h and p == "clang",
    "castxml_var_access_facts_reliable": lambda p, h: h and p == "castxml",
}

_GRID_PRODUCERS = (None, "clang", "castxml", "hybrid")


def _load_with_producer_and_header_confirmation(producer, header_confirmed):
    """Load a schema-v1 dict (below every per-flag threshold) with the
    given `ast_producer` and header-confirmation state, returning the
    UserWarning message (or None if no warning fired)."""
    d = _load_fixture("v1.json")
    d["schema_version"] = 1
    if producer is not None:
        d["ast_producer"] = producer
    if header_confirmed:
        d["from_headers"] = True
    else:
        # Omitting the key entirely -- rather than setting it False -- is
        # what triggers `from_headers_inferred=True` (a GUESSED, not
        # confirmed, header-aware side): see snapshot_from_dict's own
        # from_headers-inference comment.
        d.pop("from_headers", None)
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        snap = snapshot_from_dict(d)
    assert snap.functions, (
        "fixture must carry at least one function for from_headers to infer True"
    )
    return str(record[0].message) if record else ""


@pytest.mark.parametrize("flag_name", sorted(_FLAG_DEGRADED_FOR_PRODUCER))
@pytest.mark.parametrize("header_confirmed", [True, False])
@pytest.mark.parametrize("producer", _GRID_PRODUCERS)
def test_degraded_facts_warning_grid(producer, header_confirmed, flag_name):
    message = _load_with_producer_and_header_confirmation(producer, header_confirmed)
    degraded = _FLAG_DEGRADED_FOR_PRODUCER[flag_name](producer)
    consulted = _FLAG_CONSULTED_FOR[flag_name](producer, header_confirmed)
    expect_listed = degraded and consulted
    assert (flag_name in message) is expect_listed, (
        f"{flag_name} producer={producer!r} header_confirmed={header_confirmed}: "
        f"expected listed={expect_listed}, message={message!r}"
    )


# ---------------------------------------------------------------------------
# Documentation/constant sync — a stale docs number silently misleads readers
# about what "current" means (docs/reference/snapshot-format.md drifted to a
# hardcoded "8" while SCHEMA_VERSION had moved on to 11; this pins them together).
# ---------------------------------------------------------------------------


def test_docs_snapshot_schema_version_matches_constant():
    docs_path = (
        Path(__file__).parent.parent / "docs" / "reference" / "snapshot-format.md"
    )
    text = docs_path.read_text()
    assert f"**`{SCHEMA_VERSION}`**" in text, (
        f"docs/reference/snapshot-format.md does not mention the current "
        f"SCHEMA_VERSION ({SCHEMA_VERSION}) — update it alongside any schema bump."
    )
    assert f"(currently `{SCHEMA_VERSION}`)" in text
    # Every "currently `N`" occurrence (there are two: the schema-version
    # section and the "Two contracts" table) must agree with the constant —
    # a page can drift to a stale number in one spot while another was
    # updated (CodeRabbit review: this table's "currently `8`" survived an
    # earlier SCHEMA_VERSION bump to 11 unnoticed).
    stale = [
        m.group(0)
        for m in re.finditer(r"currently `(\d+)`", text)
        if m.group(1) != str(SCHEMA_VERSION)
    ]
    assert not stale, f"stale schema-version literal(s) in docs: {stale}"


class TestLoadSnapshotDocumentRejectsNonObjectRoot:
    """CodeRabbit review: json.loads() can return a list/str/number/bool for
    arbitrary JSON text -- load_snapshot_document()'s own dict[str, Any]
    contract (and is_sectioned_document's "sections" key lookup) both
    assume a JSON object. A non-dict root must fail loudly, not surface as
    a confusing downstream error or a wrong is_sectioned_document verdict."""

    @pytest.mark.parametrize("content", ["[1, 2, 3]", '"just a string"', "42", "null"])
    def test_non_dict_root_raises_snapshot_error(self, tmp_path, content):
        from abicheck.errors import SnapshotError
        from abicheck.serialization import load_snapshot_document

        p = tmp_path / "not_an_object.json"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(SnapshotError):
            load_snapshot_document(p)
