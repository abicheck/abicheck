# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the POST Python export-manifest adapter (``abicheck.post_manifest``)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.post_manifest import (
    PostExport,
    PostUfunc,
    check_version_gate,
    diff_manifests,
    format_diff_report,
    format_gate_report,
    format_validation_report,
    load_manifest,
    parse_manifest,
    public_c_symbols,
    validate_manifest_against_binary,
    validate_manifest_against_symbols,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _manifest_dict(exports: list[dict], post_abi: int = 1) -> dict:
    return {"post_abi": post_abi, "exports": exports}


def _gammaln_export(return_dtype: str = "Float64", params: list[str] | None = None) -> dict:
    return {
        "name": "gammaln",
        "c_symbol": "pp_gammaln",
        "kernel_symbol": "__pp_lgamma",
        "module": "_gamma",
        "kind": "alias",
        "alias_of": "lgamma",
        "params": params if params is not None else ["Float64"],
        "return_dtype": return_dtype,
        "ufunc": {"loop_symbol": "pp_gammaln_ufunc_loop", "signature": "()->()"},
    }


def _elf(symbol_names: list[str], soname: str = "libmylib.so.1") -> ElfMetadata:
    syms = [ElfSymbol(name=n, sym_type=SymbolType.FUNC) for n in symbol_names]
    return ElfMetadata(soname=soname, symbols=syms)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_minimal_manifest() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    assert m.post_abi == 1
    assert len(m.exports) == 1
    exp = m.exports[0]
    assert exp.name == "gammaln"
    assert exp.c_symbol == "pp_gammaln"
    assert exp.kernel_symbol == "__pp_lgamma"
    assert exp.params == ["Float64"]
    assert exp.return_dtype == "Float64"
    assert exp.ufunc is not None
    assert exp.ufunc.loop_symbol == "pp_gammaln_ufunc_loop"


def test_parse_missing_post_abi_raises() -> None:
    with pytest.raises(ValueError, match="post_abi"):
        parse_manifest({"exports": []})


def test_parse_missing_exports_key_raises() -> None:
    # A genuinely empty surface must spell "exports": []; a *missing* key is a
    # truncated/malformed manifest and must fail, not read as "no promised
    # symbols" (which would let validation/scoping check nothing).
    with pytest.raises(ValueError, match="exports"):
        parse_manifest({"post_abi": 1})


@pytest.mark.parametrize("missing", ["params", "return_dtype"])
def test_export_requires_signature_fields(missing: str) -> None:
    # Every export's signature is its ABI, so a missing params or return_dtype
    # must fail — normalizing it to []/"" would let a real signature change
    # vanish from the diff/version gate.
    export = {"name": "f", "c_symbol": "pp_f", "params": ["Float64"],
              "return_dtype": "Float64"}
    del export[missing]
    with pytest.raises(ValueError, match=f"missing required '{missing}'"):
        PostExport.from_dict(export)


def test_alias_export_also_requires_signature_fields() -> None:
    # Aliases are not exempt: nothing resolves alias_of when diffing, so an alias
    # that omits its signature would compare as an empty descriptor on both sides
    # and a retargeted alias could slip past the gate. The manifest must
    # materialize the signature.
    with pytest.raises(ValueError, match="missing required 'params'"):
        PostExport.from_dict({"name": "g", "c_symbol": "pp_g", "alias_of": "f"})


@pytest.mark.parametrize("bad_return", [None, False, 0, 1.5, ["Float64"]])
def test_non_string_return_dtype_is_rejected(bad_return: object) -> None:
    # "" is the documented void spelling, but a present null/false/0 would coerce
    # to the same empty descriptor and hide a real return-dtype change.
    with pytest.raises(ValueError, match="'return_dtype' must be a string"):
        PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": [],
                              "return_dtype": bad_return})


def test_void_return_dtype_empty_string_is_accepted() -> None:
    # The documented void spelling — an explicit empty string — is valid.
    exp = PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": [],
                                "return_dtype": ""})
    assert exp.return_dtype == ""


def test_parse_duplicate_c_symbol_raises() -> None:
    # Duplicate committed c_symbols are internally invalid: export_by_c_symbol()
    # keeps only the last, so a stale duplicate with the old signature after a
    # changed one would let the diff/version-gate see no break.
    with pytest.raises(ValueError, match="duplicate c_symbol"):
        parse_manifest(_manifest_dict([
            {"name": "f", "c_symbol": "pp_f", "params": ["Float64"], "return_dtype": "Float64"},
            {"name": "f", "c_symbol": "pp_f", "params": ["Int64"], "return_dtype": "Int64"},
        ]))


@pytest.mark.parametrize("post_abi", ["not-a-number", "5", 1.9, True, False, None])
def test_parse_non_integer_post_abi_raises(post_abi: object) -> None:
    # A version-gate field must be a genuine JSON integer — bool (True->1),
    # float (1.9->1), and numeric strings ("5") must be rejected, not silently
    # coerced, so a malformed contract can't pass the gate.
    with pytest.raises(ValueError, match="integer"):
        parse_manifest({"post_abi": post_abi, "exports": []})


def test_parse_tolerates_unknown_fields_and_object_params() -> None:
    # v0.x draft may add fields; params may be objects with a "dtype" key.
    m = parse_manifest(_manifest_dict([{
        "name": "add", "c_symbol": "pp_add", "future_field": {"x": 1},
        "params": [{"name": "a", "dtype": "Int64"}, {"name": "b", "dtype": "Int64"}],
        "return_dtype": "Int64",
    }]))
    assert m.exports[0].params == ["Int64", "Int64"]


def test_param_object_array_flag_is_part_of_signature() -> None:
    # Codex P1: a scalar Float64 and an array Float64 have different ABI shapes.
    scalar = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": [{"name": "x", "dtype": "Float64"}], "return_dtype": "Float64",
    }]))
    array = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": [{"name": "x", "dtype": "Float64", "is_array": True}],
        "return_dtype": "Float64",
    }]))
    # Same dtype, differing array flag -> distinct descriptors -> breaking diff.
    assert scalar.exports[0].params != array.exports[0].params
    diff = diff_manifests(scalar, array)
    assert diff.is_breaking
    assert any(c.kind == "signature" for c in diff.breaking_changes)


def test_param_false_flags_normalize_to_bare_dtype() -> None:
    # Codex P2: explicit scalar defaults (is_array: false) must NOT differ from
    # the bare dtype form — normalizing a manifest is not a breaking change.
    bare = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": ["Float64"], "return_dtype": "Float64",
    }]))
    explicit = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": [{"name": "x", "dtype": "Float64", "is_array": False,
                    "is_core_dim": False}],
        "return_dtype": "Float64",
    }]))
    assert bare.exports[0].params == explicit.exports[0].params
    assert not diff_manifests(bare, explicit).is_breaking


def test_param_unknown_metadata_is_not_part_of_signature() -> None:
    # Only known ABI-shape flags distinguish the descriptor; unknown/non-ABI
    # metadata (doc, units, draft-schema fields) must not create a false
    # breaking signature diff — the module promises tolerance of unknown keys.
    bare = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": ["Float64"], "return_dtype": "Float64",
    }]))
    annotated = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": [{"dtype": "Float64", "doc": "the input", "units": "radians"}],
        "return_dtype": "Float64",
    }]))
    assert bare.exports[0].params == annotated.exports[0].params
    assert not diff_manifests(bare, annotated).is_breaking


def test_param_true_array_flag_is_part_of_signature() -> None:
    # The positive control: a *true* ABI-shape flag must still distinguish.
    scalar = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": ["Float64"], "return_dtype": "Float64",
    }]))
    array = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": [{"dtype": "Float64", "is_array": True}], "return_dtype": "Float64",
    }]))
    assert diff_manifests(scalar, array).is_breaking


def test_non_object_export_entry_is_rejected() -> None:
    # A bare-string (non-object) export entry must fail parsing, not be silently
    # dropped — dropping it would shrink the ABI surface and let the version
    # gate pass without ever checking the promised symbol.
    with pytest.raises(ValueError, match="exports\\[0\\] must be an object"):
        parse_manifest({"post_abi": 1, "exports": ["pp_foo"]})


def test_param_name_is_not_part_of_signature() -> None:
    # Renaming a parameter (name only) is not an ABI change.
    a = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": [{"name": "x", "dtype": "Float64"}], "return_dtype": "Float64",
    }]))
    b = parse_manifest(_manifest_dict([{
        "name": "f", "c_symbol": "pp_f",
        "params": [{"name": "y", "dtype": "Float64"}], "return_dtype": "Float64",
    }]))
    assert not diff_manifests(a, b).is_breaking


def test_c_symbol_defaults_from_name() -> None:
    m = parse_manifest(_manifest_dict([{"name": "foo", "params": [], "return_dtype": "Int64"}]))
    assert m.exports[0].c_symbol == "pp_foo"


def test_export_entry_without_name_or_c_symbol_raises() -> None:
    with pytest.raises(ValueError, match="neither"):
        PostExport.from_dict({"params": []})


@pytest.mark.parametrize("params", ["Float64", "", None, False, 0])
def test_params_must_be_a_list(params: object) -> None:
    # A non-list 'params' must be rejected — a string must not silently iterate
    # into per-character params, and a falsey non-list ("", None, False, 0) must
    # not be coerced to a no-arg export (the `or []` regression path).
    with pytest.raises(ValueError, match="'params' must be an array"):
        PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": params})


def test_load_manifest_from_file(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(json.dumps(_manifest_dict([_gammaln_export()])), encoding="utf-8")
    m = load_manifest(p)
    assert m.post_abi == 1


def test_load_manifest_invalid_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_manifest(p)


# ---------------------------------------------------------------------------
# public_c_symbols
# ---------------------------------------------------------------------------


def test_public_c_symbols_includes_exports_and_ufunc_loops() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    assert public_c_symbols(m) == {"pp_gammaln", "pp_gammaln_ufunc_loop"}


def test_public_c_symbols_excludes_kernel_symbols() -> None:
    # __pp_* kernel symbols are private and must not appear in the surface.
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    assert "__pp_lgamma" not in public_c_symbols(m)


# ---------------------------------------------------------------------------
# Manifest ↔ binary validation
# ---------------------------------------------------------------------------


def test_validate_passes_when_all_symbols_exported() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    elf = _elf(["pp_gammaln", "pp_gammaln_ufunc_loop", "__pp_lgamma"])
    result = validate_manifest_against_binary(m, elf)
    assert result.passed
    assert not result.missing
    assert not result.undeclared


def test_validate_fails_on_missing_promised_symbol() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    elf = _elf(["pp_gammaln_ufunc_loop"])  # pp_gammaln absent
    result = validate_manifest_against_binary(m, elf)
    assert not result.passed
    assert result.missing == ["pp_gammaln"]


def test_validate_fails_on_missing_ufunc_loop() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    elf = _elf(["pp_gammaln"])  # loop symbol absent
    result = validate_manifest_against_binary(m, elf)
    assert not result.passed
    assert result.missing_ufunc_loops == ["pp_gammaln_ufunc_loop"]


def test_validate_reports_undeclared_pp_symbol_as_warning_not_failure() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    elf = _elf(["pp_gammaln", "pp_gammaln_ufunc_loop", "pp_secret_export"])
    result = validate_manifest_against_binary(m, elf)
    assert result.passed  # undeclared is informational
    assert result.undeclared == ["pp_secret_export"]


def test_validate_ignores_kernel_symbols_as_undeclared() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    elf = _elf(["pp_gammaln", "pp_gammaln_ufunc_loop", "__pp_internal"])
    result = validate_manifest_against_binary(m, elf)
    assert result.undeclared == []


@pytest.mark.parametrize("bad_param", [
    {"name": "x"},                       # dtype missing entirely
    {"name": "x", "dtype": ""},          # dtype empty
    {"name": "x", "type": "Float64"},    # dtype misspelled
])
def test_param_object_without_dtype_is_rejected(bad_param: dict) -> None:
    # A param object without a real dtype must fail parsing — normalizing it to
    # an empty descriptor would let diff_manifests compare two different real
    # dtypes as equal and hide a dtype ABI break.
    with pytest.raises(ValueError, match="missing required 'dtype'"):
        PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": [bad_param]})


@pytest.mark.parametrize("bad_ufunc", [["loop"], "pp_foo_loop", 5, True])
def test_malformed_ufunc_facet_is_rejected(bad_ufunc: object) -> None:
    # A present-but-non-object `ufunc` must fail parsing, not be dropped to None
    # — dropping it would remove the promised loop symbol from the committed
    # surface and let a bad manifest pass validation without checking it.
    with pytest.raises(ValueError, match="'ufunc' must be an object"):
        PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": [],
                              "return_dtype": "Float64", "ufunc": bad_ufunc})


def test_absent_or_null_ufunc_is_no_facet() -> None:
    # Absent key or explicit JSON null both mean "no ufunc facet" (not an error).
    base = {"name": "f", "c_symbol": "pp_f", "params": [], "return_dtype": "Float64"}
    assert PostExport.from_dict(base).ufunc is None
    assert PostExport.from_dict({**base, "ufunc": None}).ufunc is None


@pytest.mark.parametrize("bad_loop", [123, ["pp_loop"], {"x": 1}, True])
def test_ufunc_non_string_loop_symbol_is_rejected(bad_loop: object) -> None:
    # A truthy non-string loop_symbol would str()-coerce to a bogus committed
    # loop name, hiding a real rename/drop between two schema-drifted manifests.
    with pytest.raises(ValueError, match="'loop_symbol' must be a string"):
        PostUfunc.from_dict({"loop_symbol": bad_loop, "signature": "()->()"})


@pytest.mark.parametrize("bad_sig", [None, False, 0, ["()->()"]])
def test_ufunc_non_string_or_missing_signature_is_rejected(bad_sig: object) -> None:
    # The ufunc signature is the committed loop layout; a missing/null/non-string
    # value would coerce to "" and hide a real layout change from the gate.
    with pytest.raises(ValueError, match="'signature'"):
        PostUfunc.from_dict({"loop_symbol": "pp_f_loop", "signature": bad_sig})
    with pytest.raises(ValueError, match="missing required 'signature'"):
        PostUfunc.from_dict({"loop_symbol": "pp_f_loop"})


@pytest.mark.parametrize("ufunc", [{}, {"signature": "()->()"}, {"loop_symbol": ""}])
def test_ufunc_facet_without_loop_symbol_is_rejected(ufunc: dict) -> None:
    # A ufunc object present but missing/empty loop_symbol must fail parsing —
    # the loop symbol is the committed ABI surface, and dropping it would let
    # validation PASS without ever checking the promised loop.
    with pytest.raises(ValueError, match="missing required 'loop_symbol'"):
        PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": [],
                              "return_dtype": "Float64", "ufunc": ufunc})


def test_object_symbol_does_not_satisfy_callable_promise() -> None:
    # A promised pp_foo exported only as a data OBJECT (not FUNC/IFUNC) does not
    # satisfy clients compiled to call pp_foo(...), so validation must fail.
    m = parse_manifest(_manifest_dict([{"name": "foo", "c_symbol": "pp_foo",
                                        "params": ["Float64"], "return_dtype": "Float64"}]))
    as_object = ElfMetadata(soname="libmylib.so.1", symbols=[
        ElfSymbol(name="pp_foo", sym_type=SymbolType.OBJECT),
    ])
    result = validate_manifest_against_binary(m, as_object)
    assert result.missing == ["pp_foo"]
    assert not result.passed
    # The same name exported as a FUNC does satisfy it.
    as_func = ElfMetadata(soname="libmylib.so.1", symbols=[
        ElfSymbol(name="pp_foo", sym_type=SymbolType.FUNC),
    ])
    assert validate_manifest_against_binary(m, as_func).passed


@pytest.mark.parametrize("bad_param", [None, False, 0, 1.5, {"dtype": 0}, {"dtype": None}])
def test_non_string_param_dtype_is_rejected(bad_param: object) -> None:
    # A param is a dtype string or an object with a string dtype; a non-string
    # scalar or object dtype would coerce to a bogus descriptor and hide a real
    # dtype change, so it must fail parsing (like return_dtype).
    with pytest.raises(ValueError, match="dtype must be a string|'dtype' must be a string"):
        PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": [bad_param],
                              "return_dtype": "Float64"})


def test_empty_bare_param_dtype_is_rejected() -> None:
    # A bare "" dtype normalizes to the same empty descriptor as another empty
    # dtype, hiding a change. A no-arg export is params: [], not params: [""].
    with pytest.raises(ValueError, match="non-empty string"):
        PostExport.from_dict({"name": "f", "c_symbol": "pp_f", "params": [""],
                              "return_dtype": "Float64"})


def test_notype_wrapper_symbol_satisfies_manifest() -> None:
    # An asm/linker-defined POST wrapper exported as STT_NOTYPE is still a
    # callable entry point (dumper.py treats NOTYPE as function-like), so it must
    # satisfy the promise rather than be reported missing.
    m = parse_manifest(_manifest_dict([{"name": "foo", "c_symbol": "pp_foo",
                                        "params": ["Float64"], "return_dtype": "Float64"}]))
    as_notype = ElfMetadata(soname="libmylib.so.1", symbols=[
        ElfSymbol(name="pp_foo", sym_type=SymbolType.NOTYPE),
    ])
    assert validate_manifest_against_binary(m, as_notype).passed


def test_non_default_version_alias_does_not_satisfy_manifest() -> None:
    # A promised symbol exported only as a NON-default version alias
    # (pp_foo@POST_1, is_default=False) does not satisfy an unversioned consumer
    # link, so validation must report it missing — not pass silently.
    m = parse_manifest(_manifest_dict([{"name": "foo", "c_symbol": "pp_foo",
                                        "params": ["Float64"], "return_dtype": "Float64"}]))
    non_default = ElfMetadata(soname="libmylib.so.1", symbols=[
        ElfSymbol(name="pp_foo", sym_type=SymbolType.FUNC,
                  version="POST_1", is_default=False),
    ])
    result = validate_manifest_against_binary(m, non_default)
    assert result.missing == ["pp_foo"]
    assert not result.passed
    # The default-versioned form (pp_foo@@POST_1) DOES satisfy it.
    default_versioned = ElfMetadata(soname="libmylib.so.1", symbols=[
        ElfSymbol(name="pp_foo", sym_type=SymbolType.FUNC,
                  version="POST_1", is_default=True),
    ])
    assert validate_manifest_against_binary(m, default_versioned).passed


def test_format_validation_report_pass_and_fail() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    ok = validate_manifest_against_binary(m, _elf(["pp_gammaln", "pp_gammaln_ufunc_loop"]))
    assert "Result: PASS" in format_validation_report(ok)
    bad = validate_manifest_against_binary(m, _elf([]))
    report = format_validation_report(bad)
    assert "Result: FAIL" in report
    assert "pp_gammaln" in report


# ---------------------------------------------------------------------------
# Manifest ↔ manifest diff
# ---------------------------------------------------------------------------


def test_diff_no_changes() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export()]))
    new = parse_manifest(_manifest_dict([_gammaln_export()]))
    diff = diff_manifests(old, new)
    assert not diff.is_breaking
    assert diff.changes == []


def test_diff_removed_export_is_breaking() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export()]))
    new = parse_manifest(_manifest_dict([]))
    diff = diff_manifests(old, new)
    assert diff.is_breaking
    assert diff.breaking_changes[0].kind == "removed"


def test_diff_added_export_is_compatible() -> None:
    old = parse_manifest(_manifest_dict([]))
    new = parse_manifest(_manifest_dict([_gammaln_export()]))
    diff = diff_manifests(old, new)
    assert not diff.is_breaking
    assert diff.changes[0].kind == "added"


def test_diff_signature_change_is_breaking() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export(return_dtype="Float64")]))
    new = parse_manifest(_manifest_dict([_gammaln_export(return_dtype="Float32")]))
    diff = diff_manifests(old, new)
    assert diff.is_breaking
    change = diff.breaking_changes[0]
    assert change.kind == "signature"
    assert "Float64" in change.detail and "Float32" in change.detail


def test_diff_param_change_is_breaking() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export(params=["Float64"])]))
    new = parse_manifest(_manifest_dict([_gammaln_export(params=["Float64", "Int64"])]))
    diff = diff_manifests(old, new)
    assert diff.is_breaking
    assert diff.breaking_changes[0].kind == "signature"


def test_diff_ufunc_signature_change_is_breaking() -> None:
    old_exp = _gammaln_export()
    new_exp = _gammaln_export()
    new_exp["ufunc"] = {"loop_symbol": "pp_gammaln_ufunc_loop", "signature": "(n)->(n)"}
    diff = diff_manifests(
        parse_manifest(_manifest_dict([old_exp])),
        parse_manifest(_manifest_dict([new_exp])),
    )
    assert diff.is_breaking
    assert any(c.kind == "ufunc_signature" for c in diff.breaking_changes)


def test_format_diff_report_marks_breaks() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export()]))
    new = parse_manifest(_manifest_dict([]))
    report = format_diff_report(diff_manifests(old, new), "old.json", "new.json")
    assert "BREAK" in report
    assert "1 breaking change" in report


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------


def test_gate_passes_when_no_breaking_change() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export()], post_abi=1))
    new = parse_manifest(_manifest_dict([_gammaln_export()], post_abi=1))
    result = check_version_gate(old, new)
    assert result.passed
    assert not result.violated


def test_gate_violated_on_breaking_change_without_bump() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export()], post_abi=1))
    new = parse_manifest(_manifest_dict([], post_abi=1))  # removed export, same abi
    result = check_version_gate(old, new)
    assert result.violated
    assert not result.passed
    report = format_gate_report(result, "old.json", "new.json")
    assert "VIOLATION" in report


def test_gate_passes_when_breaking_change_covered_by_bump() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export()], post_abi=1))
    new = parse_manifest(_manifest_dict([], post_abi=2))  # removed export, abi bumped
    result = check_version_gate(old, new)
    assert result.passed
    assert not result.violated
    report = format_gate_report(result, "old.json", "new.json")
    assert "OK" in report


def test_gate_report_no_breaking_changes_message() -> None:
    old = parse_manifest(_manifest_dict([_gammaln_export()], post_abi=1))
    new = parse_manifest(_manifest_dict([_gammaln_export(), {
        "name": "extra", "c_symbol": "pp_extra", "params": [], "return_dtype": "Int64",
    }], post_abi=1))
    result = check_version_gate(old, new)
    assert result.passed
    assert "no breaking changes" in format_gate_report(result, "a", "b").lower()


# ---------------------------------------------------------------------------
# ufunc loop-symbol rename/drop is breaking (Codex P2 regression guard)
# ---------------------------------------------------------------------------


def test_diff_ufunc_loop_symbol_rename_is_breaking() -> None:
    old_exp = _gammaln_export()
    new_exp = _gammaln_export()
    # Same layout signature, renamed loop symbol — must still be a break.
    new_exp["ufunc"] = {"loop_symbol": "pp_gammaln_loop_v2", "signature": "()->()"}
    diff = diff_manifests(
        parse_manifest(_manifest_dict([old_exp])),
        parse_manifest(_manifest_dict([new_exp])),
    )
    assert diff.is_breaking
    change = next(c for c in diff.breaking_changes if c.kind == "ufunc_loop_symbol")
    assert "pp_gammaln_ufunc_loop" in change.detail
    assert "pp_gammaln_loop_v2" in change.detail


def test_diff_ufunc_loop_symbol_dropped_is_breaking() -> None:
    old_exp = _gammaln_export()
    new_exp = _gammaln_export()
    del new_exp["ufunc"]  # export keeps its c_symbol but drops the ufunc loop
    diff = diff_manifests(
        parse_manifest(_manifest_dict([old_exp])),
        parse_manifest(_manifest_dict([new_exp])),
    )
    assert diff.is_breaking
    assert any(c.kind == "ufunc_loop_symbol" for c in diff.breaking_changes)


# ---------------------------------------------------------------------------
# Format-agnostic core + PE/Mach-O extraction branches
# ---------------------------------------------------------------------------


def test_validate_against_symbols_core_pass_and_fail() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    ok = validate_manifest_against_symbols(
        m, {"pp_gammaln", "pp_gammaln_ufunc_loop"}, library="mylib.dll")
    assert ok.passed and ok.library == "mylib.dll"
    bad = validate_manifest_against_symbols(m, set(), library="")
    assert not bad.passed and bad.library == "UNKNOWN"


def test_exported_names_for_binary_dispatches_per_format(monkeypatch, tmp_path: Path) -> None:
    from abicheck import post_manifest as pm
    from abicheck.macho_metadata import MachoExport, MachoMetadata
    from abicheck.pe_metadata import PeExport, PeMetadata

    dummy = tmp_path / "lib.bin"
    dummy.write_text("x", encoding="utf-8")
    monkeypatch.setattr(pm, "_exported_symbol_names", lambda meta: {"pp_elf"})

    def fake_normalize(path):  # noqa: ANN001
        return path, None

    monkeypatch.setattr("abicheck.binary_utils.normalize_binary_input", fake_normalize)

    # PE branch
    monkeypatch.setattr("abicheck.binary_utils.detect_binary_format", lambda p: "pe")
    monkeypatch.setattr("abicheck.pe_metadata.parse_pe_metadata",
                        lambda p: PeMetadata(exports=[PeExport(name="pp_pe")]))
    names, _ = pm._exported_names_for_binary(dummy)
    assert names == {"pp_pe"}

    # Mach-O branch — __DATA globals (is_data) are excluded, mirroring the ELF
    # OBJECT filter: only callable exports satisfy a POST wrapper promise.
    monkeypatch.setattr("abicheck.binary_utils.detect_binary_format", lambda p: "macho")
    monkeypatch.setattr(
        "abicheck.macho_metadata.parse_macho_metadata",
        lambda p: MachoMetadata(exports=[MachoExport(name="pp_macho"),
                                         MachoExport(name="pp_data", is_data=True)],
                                install_name="libx.dylib"),
    )
    names, label = pm._exported_names_for_binary(dummy)
    assert names == {"pp_macho"} and label == "libx.dylib"


def test_exported_names_for_binary_unknown_format_raises(monkeypatch, tmp_path: Path) -> None:
    from abicheck import post_manifest as pm

    dummy = tmp_path / "lib.bin"
    dummy.write_text("x", encoding="utf-8")
    monkeypatch.setattr("abicheck.binary_utils.normalize_binary_input",
                        lambda p: (p, "wasm"))
    with pytest.raises(ValueError, match="ELF/PE/Mach-O"):
        pm._exported_names_for_binary(dummy)


# ---------------------------------------------------------------------------
# Remaining format/branch coverage
# ---------------------------------------------------------------------------


def test_format_diff_report_no_changes() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    report = format_diff_report(diff_manifests(m, m), "a", "b")
    assert "no export changes" in report


def test_format_validation_report_missing_ufunc_and_undeclared() -> None:
    m = parse_manifest(_manifest_dict([_gammaln_export()]))
    result = validate_manifest_against_symbols(m, {"pp_gammaln", "pp_other"})
    report = format_validation_report(result)
    assert "pp_gammaln_ufunc_loop" in report  # missing ufunc loop listed
    assert "pp_other" in report  # undeclared listed


def test_exported_names_for_binary_elf_branch(monkeypatch, tmp_path: Path) -> None:
    from abicheck import post_manifest as pm
    from abicheck.elf_metadata import ElfMetadata

    dummy = tmp_path / "lib.so"
    dummy.write_text("x", encoding="utf-8")
    monkeypatch.setattr("abicheck.binary_utils.normalize_binary_input",
                        lambda p: (p, "elf"))
    monkeypatch.setattr("abicheck.elf_metadata.parse_elf_metadata",
                        lambda p: ElfMetadata(soname="libz.so.1", symbols=[]))
    monkeypatch.setattr(pm, "_exported_symbol_names", lambda meta: {"pp_z"})
    names, label = pm._exported_names_for_binary(dummy)
    assert names == {"pp_z"} and label == "libz.so.1"


def test_validate_from_binary_end_to_end(monkeypatch, tmp_path: Path) -> None:
    from abicheck import post_manifest as pm
    from abicheck.post_manifest import validate_from_binary

    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(_manifest_dict([_gammaln_export()])), encoding="utf-8")
    dummy = tmp_path / "lib.so"
    dummy.write_text("x", encoding="utf-8")
    monkeypatch.setattr(pm, "_exported_names_for_binary",
                        lambda p: ({"pp_gammaln", "pp_gammaln_ufunc_loop"}, "libx.so"))
    result = validate_from_binary(manifest, dummy)
    assert result.passed and result.library == "libx.so"


def test_fmt_sig_void_return() -> None:
    m = parse_manifest(_manifest_dict([
        {"name": "sink", "c_symbol": "pp_sink", "params": ["Int64"], "return_dtype": ""},
    ]))
    m2 = parse_manifest(_manifest_dict([
        {"name": "sink", "c_symbol": "pp_sink", "params": [], "return_dtype": ""},
    ]))
    report = format_diff_report(diff_manifests(m, m2), "a", "b")
    assert "void" in report


def test_diff_adding_ufunc_facet_is_compatible() -> None:
    # Codex P2: adding a ufunc facet to a previously-scalar export is a
    # compatible addition — old clients could not have linked to a loop symbol
    # that did not exist yet. Must NOT require a post_abi bump.
    scalar = _gammaln_export()
    del scalar["ufunc"]  # v1: scalar export, no ufunc
    vectorized = _gammaln_export()  # v2: same signature, adds the ufunc facet
    diff = diff_manifests(
        parse_manifest(_manifest_dict([scalar])),
        parse_manifest(_manifest_dict([vectorized])),
    )
    assert not diff.is_breaking
    # The added facet must still be *reported* as a compatible change, so an
    # implementation that silently drops the new public loop symbol is caught.
    assert any(c.kind == "ufunc_added" for c in diff.changes)


def test_diff_removing_ufunc_facet_is_breaking() -> None:
    # The inverse: dropping an existing ufunc facet removes a committed loop
    # symbol and breaks clients linked to it.
    vectorized = _gammaln_export()
    scalar = _gammaln_export()
    del scalar["ufunc"]
    diff = diff_manifests(
        parse_manifest(_manifest_dict([vectorized])),
        parse_manifest(_manifest_dict([scalar])),
    )
    assert diff.is_breaking
    assert any(c.kind == "ufunc_loop_symbol" for c in diff.breaking_changes)
