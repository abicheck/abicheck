"""Tests for abi_check.serialization — roundtrip JSON."""
import tempfile
from pathlib import Path

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
)


def _sample_snap() -> AbiSnapshot:
    return AbiSnapshot(
        library="libsample.so.2",
        version="2.3.1",
        functions=[
            Function(
                name="sample_init",
                mangled="_Z11sample_initv",
                return_type="int",
                visibility=Visibility.PUBLIC,
                is_noexcept=True,
            )
        ],
    )


class TestSerialization:
    def test_roundtrip_dict(self):
        snap = _sample_snap()
        d = snapshot_to_dict(snap)
        snap2 = snapshot_from_dict(d)
        assert snap2.library == snap.library
        assert snap2.version == snap.version
        assert len(snap2.functions) == 1
        assert snap2.functions[0].mangled == "_Z11sample_initv"
        assert snap2.functions[0].is_noexcept is True

    def test_roundtrip_file(self):
        snap = _sample_snap()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            save_snapshot(snap, tmp)
            snap2 = load_snapshot(tmp)
            assert snap2.version == snap.version
            assert snap2.functions[0].name == "sample_init"
        finally:
            tmp.unlink(missing_ok=True)

    def test_private_index_fields_not_serialized(self):
        snap = _sample_snap()
        d = snapshot_to_dict(snap)
        assert "_func_by_mangled" not in d
        assert "_var_by_mangled" not in d

    def test_snapshot_to_dict_does_not_mutate_caller_snapshot(self):
        """snapshot_to_dict must be pure: it used to reset the lazy lookup
        caches on the caller's actual snapshot object (not a copy) as a side
        effect, so calling it after the caller had built an index would
        silently invalidate that index out from under them."""
        snap = _sample_snap()
        snap.index()
        assert snap._func_by_mangled is not None
        assert snap._var_by_mangled is not None
        assert snap._type_by_name is not None

        snapshot_to_dict(snap)

        assert snap._func_by_mangled is not None
        assert snap._var_by_mangled is not None
        assert snap._type_by_name is not None
        assert snap._func_by_mangled["_Z11sample_initv"].name == "sample_init"

    def test_is_inline_roundtrip(self):
        """is_inline=True must survive snapshot_to_dict → snapshot_from_dict roundtrip."""
        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="fast",
                    mangled="_Z4fasti",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    is_inline=True,
                )
            ],
        )
        d = snapshot_to_dict(snap)
        assert d["functions"][0]["is_inline"] is True
        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].is_inline is True

    def test_is_inline_false_roundtrip(self):
        """is_inline=False must survive snapshot_to_dict → snapshot_from_dict roundtrip."""
        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="compute",
                    mangled="_Z7computei",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    is_inline=False,
                )
            ],
        )
        d = snapshot_to_dict(snap)
        assert d["functions"][0]["is_inline"] is False
        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].is_inline is False


class TestSerializationRoundtripExtended:
    """B11: Param.default + TypeField qualifiers roundtrip (QA gap from PR #87 review)."""

    def test_param_default_roundtrip(self) -> None:
        """Param with default='42' must survive snapshot_to_dict → snapshot_from_dict.

        default stores the string representation of the default argument.
        This must survive roundtrip without loss.
        """
        from abicheck.model import Param, ParamKind

        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="create",
                    mangled="_Z6createi",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    params=[
                        Param(name="count", type="int", kind=ParamKind.VALUE, default="42"),
                        Param(name="flag", type="bool", kind=ParamKind.VALUE, default="true"),
                        Param(name="name", type="const char*", kind=ParamKind.POINTER, default=None),
                    ],
                )
            ],
        )
        d = snapshot_to_dict(snap)
        params_raw = d["functions"][0]["params"]
        assert params_raw[0]["default"] == "42"
        assert params_raw[1]["default"] == "true"
        assert params_raw[2]["default"] is None

        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].params[0].default == "42"
        assert snap2.functions[0].params[1].default == "true"
        assert snap2.functions[0].params[2].default is None

    def test_typefield_qualifiers_roundtrip(self) -> None:
        """TypeField with is_const, is_volatile, is_mutable must survive roundtrip.

        All three qualifiers must be preserved faithfully through serialization.
        """
        from abicheck.model import RecordType, TypeField

        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            types=[
                RecordType(
                    name="QualifiedFields",
                    kind="struct",
                    fields=[
                        TypeField(
                            name="c_field",
                            type="int",
                            is_const=True,
                            is_volatile=False,
                            is_mutable=False,
                        ),
                        TypeField(
                            name="v_field",
                            type="int",
                            is_const=False,
                            is_volatile=True,
                            is_mutable=False,
                        ),
                        TypeField(
                            name="m_field",
                            type="int",
                            is_const=False,
                            is_volatile=False,
                            is_mutable=True,
                        ),
                        TypeField(
                            name="cvm_field",
                            type="int",
                            is_const=True,
                            is_volatile=True,
                            is_mutable=True,
                        ),
                        TypeField(
                            name="plain_field",
                            type="int",
                            is_const=False,
                            is_volatile=False,
                            is_mutable=False,
                        ),
                    ],
                )
            ],
        )
        d = snapshot_to_dict(snap)
        fields_raw = d["types"][0]["fields"]

        # Verify serialization
        assert fields_raw[0]["is_const"] is True
        assert fields_raw[0]["is_volatile"] is False
        assert fields_raw[0]["is_mutable"] is False

        assert fields_raw[1]["is_const"] is False
        assert fields_raw[1]["is_volatile"] is True
        assert fields_raw[1]["is_mutable"] is False

        assert fields_raw[2]["is_const"] is False
        assert fields_raw[2]["is_volatile"] is False
        assert fields_raw[2]["is_mutable"] is True

        assert fields_raw[3]["is_const"] is True
        assert fields_raw[3]["is_volatile"] is True
        assert fields_raw[3]["is_mutable"] is True

        assert fields_raw[4]["is_const"] is False
        assert fields_raw[4]["is_volatile"] is False
        assert fields_raw[4]["is_mutable"] is False

        # Verify deserialization
        snap2 = snapshot_from_dict(d)
        f = snap2.types[0].fields
        assert f[0].is_const is True and f[0].is_volatile is False and f[0].is_mutable is False
        assert f[1].is_const is False and f[1].is_volatile is True and f[1].is_mutable is False
        assert f[2].is_const is False and f[2].is_volatile is False and f[2].is_mutable is True
        assert f[3].is_const is True and f[3].is_volatile is True and f[3].is_mutable is True
        assert f[4].is_const is False and f[4].is_volatile is False and f[4].is_mutable is False

    def test_is_template_pattern_roundtrip(self) -> None:
        """RecordType.is_template_pattern must survive a load→save cycle.

        Regression: snapshot_from_dict originally omitted this field when
        reconstructing RecordType, so a clang snapshot's template-pattern
        marker was silently dropped on reload — re-enabling the exact
        bare-name layout-backfill risk the flag exists to prevent (Codex
        review).
        """
        from abicheck.model import RecordType

        snap = AbiSnapshot(
            library="libtemplate.so.1",
            version="1.0",
            types=[
                RecordType(name="Buffer", kind="class", is_template_pattern=True),
                RecordType(name="Point", kind="struct", is_template_pattern=False),
            ],
        )
        d = snapshot_to_dict(snap)
        assert d["types"][0]["is_template_pattern"] is True
        assert d["types"][1]["is_template_pattern"] is False

        snap2 = snapshot_from_dict(d)
        assert snap2.types[0].is_template_pattern is True
        assert snap2.types[1].is_template_pattern is False

    def test_has_anonymous_aggregate_fields_roundtrip(self) -> None:
        """RecordType.has_anonymous_aggregate_fields must survive a
        load->save cycle, mirroring the is_template_pattern regression above:
        the DWARF layout backfill relies on this flag being present after
        reload to trust a namespaced anonymous-aggregate record's suffix
        match (Codex review).
        """
        from abicheck.model import RecordType

        snap = AbiSnapshot(
            library="libanon.so.1",
            version="1.0",
            types=[
                RecordType(name="Foo", kind="struct", has_anonymous_aggregate_fields=True),
                RecordType(name="Bar", kind="struct", has_anonymous_aggregate_fields=False),
            ],
        )
        d = snapshot_to_dict(snap)
        assert d["types"][0]["has_anonymous_aggregate_fields"] is True
        assert d["types"][1]["has_anonymous_aggregate_fields"] is False

        snap2 = snapshot_from_dict(d)
        assert snap2.types[0].has_anonymous_aggregate_fields is True
        assert snap2.types[1].has_anonymous_aggregate_fields is False

    def test_param_default_none_roundtrip(self) -> None:
        """Param.default=None (no default) must round-trip correctly."""
        from abicheck.model import Param

        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="init",
                    mangled="_Z4initi",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                    params=[Param(name="x", type="int", default=None)],
                )
            ],
        )
        d = snapshot_to_dict(snap)
        assert d["functions"][0]["params"][0]["default"] is None
        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].params[0].default is None


class TestFromHeadersBackCompat:
    """from_headers provenance loading, incl. legacy snapshots without the key."""

    def test_present_key_true_is_honored(self):
        d = {"library": "libfoo.so", "version": "1.0", "from_headers": True}
        assert snapshot_from_dict(d).from_headers is True

    def test_present_key_false_is_honored(self):
        # A current DWARF-only/symbols-only dump legitimately carries False.
        d = {
            "library": "libfoo.so", "version": "1.0", "from_headers": False,
            "functions": [{"name": "f", "mangled": "f", "return_type": "int"}],
        }
        assert snapshot_from_dict(d).from_headers is False

    def test_legacy_no_key_with_surface_infers_header_aware(self):
        # Legacy snapshot (no from_headers key) that was dumped with headers:
        # a populated, non-elf-only surface must keep prior HEADER_AWARE behavior.
        d = {
            "library": "libfoo.so", "version": "1.0",
            "functions": [{"name": "f", "mangled": "f", "return_type": "int"}],
        }
        assert "from_headers" not in d
        assert snapshot_from_dict(d).from_headers is True

    def test_legacy_no_key_elf_only_mode_stays_false(self):
        d = {
            "library": "libfoo.so", "version": "1.0", "elf_only_mode": True,
            "functions": [{"name": "f", "mangled": "f", "return_type": "int"}],
        }
        assert snapshot_from_dict(d).from_headers is False

    def test_legacy_no_key_empty_surface_stays_false(self):
        d = {"library": "libfoo.so", "version": "1.0"}
        assert snapshot_from_dict(d).from_headers is False
