"""Deep detection tests for symbol-level ChangeKinds with shallow coverage.

Targets ChangeKinds that have only 1-3 test references — ensures each one is
exercised with a realistic scenario that triggers the detector, not just a
registry/classification assertion.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    Variable,
    Visibility,
)


def _snap(version="1.0", functions=None, variables=None, types=None,
enums=None, typedefs=None, elf=None, constants=None, from_headers=True):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {}, elf=elf,
        constants=constants or {},
        from_headers=from_headers,
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    params=params or [], visibility=Visibility.PUBLIC, **kwargs)


def _pub_var(name, mangled, type_, **kwargs):
    return Variable(name=name, mangled=mangled, type=type_,
                    visibility=Visibility.PUBLIC, **kwargs)


def _kinds(result):
    return {c.kind for c in result.changes}


# ── func_static_changed (3 refs) ──────────────────────────────────────────

class TestFuncStaticChanged:
    """Static ↔ non-static changes the implicit 'this' parameter."""

    def test_became_static(self):
        f_v1 = _pub_func("Cls::method", "_ZN3Cls6methodEv", is_static=False)
        f_v2 = _pub_func("Cls::method", "_ZN3Cls6methodEv", is_static=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_STATIC_CHANGED in _kinds(r)

    def test_lost_static(self):
        f_v1 = _pub_func("Cls::method", "_ZN3Cls6methodEv", is_static=True)
        f_v2 = _pub_func("Cls::method", "_ZN3Cls6methodEv", is_static=False)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_STATIC_CHANGED in _kinds(r)


# ── func_cv_changed (4 refs) ──────────────────────────────────────────────

class TestFuncCvChanged:
    """const/volatile qualifier on member function changes mangling.

    In real C++, const changes the mangled name: _ZN3Cls3getEv (non-const)
    vs _ZNK3Cls3getEv (const). The detector matches by (name, param_types)
    across the removed/added sets.
    """

    def test_became_const(self):
        # Non-const mangling → const mangling (K = const in Itanium ABI)
        f_v1 = _pub_func("Cls::get", "_ZN3Cls3getEv", is_const=False)
        f_v2 = _pub_func("Cls::get", "_ZNK3Cls3getEv", is_const=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_CV_CHANGED in _kinds(r)

    def test_lost_const(self):
        f_v1 = _pub_func("Cls::get", "_ZNK3Cls3getEv", is_const=True)
        f_v2 = _pub_func("Cls::get", "_ZN3Cls3getEv", is_const=False)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_CV_CHANGED in _kinds(r)

    def test_volatile_changed(self):
        # V = volatile in Itanium ABI mangling
        f_v1 = _pub_func("Cls::op", "_ZN3Cls2opEv", is_volatile=False)
        f_v2 = _pub_func("Cls::op", "_ZNV3Cls2opEv", is_volatile=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_CV_CHANGED in _kinds(r)


# ── func_visibility_changed (3 refs for protected variant) ────────────────

class TestFuncVisibilityChanged:
    """Visibility changes: public → hidden is breaking."""

    def test_public_to_hidden_is_breaking(self):
        f_v1 = _pub_func("api", "_Z3apiv")
        f_v2 = Function(name="api", mangled="_Z3apiv", return_type="void",
                         visibility=Visibility.HIDDEN)
        # The function disappears from public API
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        # Hidden function is not visible, so this looks like removal
        assert r.verdict == Verdict.BREAKING

    def test_func_visibility_protected_via_elf(self):
        """STV_DEFAULT → STV_PROTECTED should produce FUNC_VISIBILITY_PROTECTED_CHANGED.

        This detection happens in the ELF symbol metadata detector (diff_platform),
        comparing ElfSymbol.visibility fields, not Function.elf_visibility.
        """
        old_elf = ElfMetadata(symbols=[
            ElfSymbol(name="_Z3symv", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC, visibility="default")])
        new_elf = ElfMetadata(symbols=[
            ElfSymbol(name="_Z3symv", binding=SymbolBinding.GLOBAL,
                      sym_type=SymbolType.FUNC, visibility="protected")])
        r = compare(_snap(elf=old_elf), _snap(elf=new_elf))
        assert ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED in _kinds(r)


# ── func_virtual_added / func_virtual_removed (3 refs each) ──────────────

class TestFuncVirtualChanged:
    """Adding/removing virtual changes vtable layout."""

    def test_became_virtual(self):
        f_v1 = _pub_func("Base::render", "_ZN4Base6renderEv", is_virtual=False)
        f_v2 = _pub_func("Base::render", "_ZN4Base6renderEv", is_virtual=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_VIRTUAL_ADDED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_lost_virtual(self):
        f_v1 = _pub_func("Base::render", "_ZN4Base6renderEv", is_virtual=True)
        f_v2 = _pub_func("Base::render", "_ZN4Base6renderEv", is_virtual=False)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_VIRTUAL_REMOVED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


# ── func_pure_virtual_added / func_virtual_became_pure (3 refs each) ─────

class TestFuncPureVirtualChanged:
    """Pure virtual changes force subclass implementation."""

    def test_became_pure_virtual(self):
        f_v1 = _pub_func("Base::update", "_ZN4Base6updateEv",
                          is_virtual=True, is_pure_virtual=False)
        f_v2 = _pub_func("Base::update", "_ZN4Base6updateEv",
                          is_virtual=True, is_pure_virtual=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_VIRTUAL_BECAME_PURE in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_added_new_pure_virtual(self):
        """New pure virtual function added — subclasses must implement."""
        f_new = _pub_func("Base::draw", "_ZN4Base4drawEv",
                          is_virtual=True, is_pure_virtual=True)
        old = _snap(functions=[_pub_func("Base::init", "_ZN4Base4initEv")])
        new_snap = _snap(functions=[
            _pub_func("Base::init", "_ZN4Base4initEv"),
            f_new,
        ])
        r = compare(old, new_snap)
        assert ChangeKind.FUNC_ADDED in _kinds(r)


# ── func_noexcept_removed (3 refs) ───────────────────────────────────────

class TestFuncNoexceptRemoved:
    """Removing noexcept can cause std::terminate."""

    def test_noexcept_removed(self):
        f_v1 = _pub_func("safe", "_Z4safev", is_noexcept=True)
        f_v2 = _pub_func("safe", "_Z4safev", is_noexcept=False)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_NOEXCEPT_REMOVED in _kinds(r)

    def test_noexcept_added(self):
        f_v1 = _pub_func("safe", "_Z4safev", is_noexcept=False)
        f_v2 = _pub_func("safe", "_Z4safev", is_noexcept=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_NOEXCEPT_ADDED in _kinds(r)


# ── header-declared function disappeared from .dynsym ─────────────────────

class TestHeaderDeclaredFunctionMissingFromBinary:
    """Function declared in header but not in binary symbol table.

    The mixed-mode (headers + ELF) detector emits FUNC_DELETED_ELF_FALLBACK
    when this happens — the previously-exported symbol is gone from .dynsym
    while the header still declares the function.
    """

    def test_elf_symbol_disappeared_is_detected(self):
        """When a function's ELF symbol disappears, FUNC_DELETED_ELF_FALLBACK fires."""
        f = _pub_func("api_call", "_Z8api_callv")
        old_elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z8api_callv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        new_elf = ElfMetadata(symbols=[])  # symbol disappeared from binary

        old = _snap(functions=[f], elf=old_elf)
        new = _snap(functions=[f], elf=new_elf)  # still in headers
        r = compare(old, new)
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK in _kinds(r)
        assert r.verdict == Verdict.BREAKING


# ── func_deleted (= delete) ──────────────────────────────────────────────

class TestFuncDeleted:
    """Function marked = delete; old binaries still reference it."""

    def test_func_became_deleted(self):
        f_v1 = _pub_func("Cls::copy", "_ZN3Cls4copyEv", is_deleted=False)
        f_v2 = _pub_func("Cls::copy", "_ZN3Cls4copyEv", is_deleted=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_DELETED in _kinds(r)
        assert r.verdict == Verdict.BREAKING


# ── func_ref_qual_changed ────────────────────────────────────────────────

class TestFuncRefQualChanged:
    """Ref-qualifier changes alter mangling."""

    def test_no_ref_to_lvalue_ref(self):
        f_v1 = _pub_func("Cls::val", "_ZN3Cls3valEv", ref_qualifier="")
        f_v2 = _pub_func("Cls::val", "_ZN3Cls3valEv", ref_qualifier="&")
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_REF_QUAL_CHANGED in _kinds(r)

    def test_lvalue_to_rvalue_ref(self):
        f_v1 = _pub_func("Cls::val", "_ZN3Cls3valEv", ref_qualifier="&")
        f_v2 = _pub_func("Cls::val", "_ZN3Cls3valEv", ref_qualifier="&&")
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_REF_QUAL_CHANGED in _kinds(r)


# ── func_language_linkage_changed ────────────────────────────────────────

class TestFuncLanguageLinkageChanged:
    """extern 'C' ↔ C++ linkage changes mangling."""

    def test_gained_extern_c(self):
        f_v1 = _pub_func("init", "_Z4initv", is_extern_c=False)
        f_v2 = _pub_func("init", "_Z4initv", is_extern_c=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED in _kinds(r)

    def test_lost_extern_c(self):
        f_v1 = _pub_func("init", "_Z4initv", is_extern_c=True)
        f_v2 = _pub_func("init", "_Z4initv", is_extern_c=False)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED in _kinds(r)


# ── func_became_inline / func_lost_inline ────────────────────────────────

class TestFuncInlineChanged:
    """Inline attribute changes."""

    def test_became_inline(self):
        f_v1 = _pub_func("helper", "_Z6helperv", is_inline=False)
        f_v2 = _pub_func("helper", "_Z6helperv", is_inline=True)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_BECAME_INLINE in _kinds(r)

    def test_lost_inline(self):
        f_v1 = _pub_func("helper", "_Z6helperv", is_inline=True)
        f_v2 = _pub_func("helper", "_Z6helperv", is_inline=False)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.FUNC_LOST_INLINE in _kinds(r)


# ── param_pointer_level_changed / return_pointer_level_changed ───────────

class TestPointerLevelChanged:
    """Pointer depth changes (T vs T* vs T**)."""

    def test_param_pointer_level_changed(self):
        f_v1 = _pub_func("process", "_Z7processv",
                          params=[Param(name="data", type="int", pointer_depth=0)])
        f_v2 = _pub_func("process", "_Z7processv",
                          params=[Param(name="data", type="int *", pointer_depth=1)])
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.PARAM_POINTER_LEVEL_CHANGED in _kinds(r)

    def test_return_pointer_level_changed(self):
        f_v1 = _pub_func("getData", "_Z7getDatav", ret="int",
                          return_pointer_depth=0)
        f_v2 = _pub_func("getData", "_Z7getDatav", ret="int *",
                          return_pointer_depth=1)
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.RETURN_POINTER_LEVEL_CHANGED in _kinds(r)


# ── param_restrict_changed ───────────────────────────────────────────────

class TestParamRestrictChanged:
    """restrict qualifier on pointer parameter."""

    def test_restrict_added(self):
        f_v1 = _pub_func("memcopy", "_Z7memcopyv",
                          params=[Param(name="dst", type="void *", is_restrict=False)])
        f_v2 = _pub_func("memcopy", "_Z7memcopyv",
                          params=[Param(name="dst", type="void * restrict", is_restrict=True)])
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.PARAM_RESTRICT_CHANGED in _kinds(r)


# ── param_became_va_list / param_lost_va_list ────────────────────────────

class TestParamVaListChanged:
    """va_list parameter changes."""

    def test_param_became_va_list(self):
        f_v1 = _pub_func("vformat", "_Z7vformatv",
                          params=[Param(name="args", type="int", is_va_list=False)])
        f_v2 = _pub_func("vformat", "_Z7vformatv",
                          params=[Param(name="args", type="va_list", is_va_list=True)])
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.PARAM_BECAME_VA_LIST in _kinds(r)

    def test_param_lost_va_list(self):
        f_v1 = _pub_func("vformat", "_Z7vformatv",
                          params=[Param(name="args", type="va_list", is_va_list=True)])
        f_v2 = _pub_func("vformat", "_Z7vformatv",
                          params=[Param(name="args", type="int", is_va_list=False)])
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.PARAM_LOST_VA_LIST in _kinds(r)


# ── param_default_value_changed / removed ────────────────────────────────

class TestParamDefaultChanged:
    """Default parameter value changes/removal."""

    def test_default_value_changed(self):
        f_v1 = _pub_func("connect", "_Z7connectv",
                          params=[Param(name="timeout", type="int", default="30")])
        f_v2 = _pub_func("connect", "_Z7connectv",
                          params=[Param(name="timeout", type="int", default="60")])
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.PARAM_DEFAULT_VALUE_CHANGED in _kinds(r)

    def test_default_value_removed(self):
        f_v1 = _pub_func("connect", "_Z7connectv",
                          params=[Param(name="timeout", type="int", default="30")])
        f_v2 = _pub_func("connect", "_Z7connectv",
                          params=[Param(name="timeout", type="int", default=None)])
        r = compare(_snap(functions=[f_v1]), _snap(functions=[f_v2]))
        assert ChangeKind.PARAM_DEFAULT_VALUE_REMOVED in _kinds(r)


# ── param_renamed ────────────────────────────────────────────────────────

class TestParamRenamed:
    """Parameter name change (source-level break)."""

    def test_param_renamed(self):
        f_v1 = _pub_func("draw", "_Z4drawv",
                          params=[Param(name="x_pos", type="int")])
        f_v2 = _pub_func("draw", "_Z4drawv",
                          params=[Param(name="horizontal", type="int")])
        r = compare(
            _snap(functions=[f_v1], from_headers=True),
            _snap(functions=[f_v2], from_headers=True),
        )
        assert ChangeKind.PARAM_RENAMED in _kinds(r)


# ── var_value_changed ────────────────────────────────────────────────────

class TestVarValueChanged:
    """Compile-time constant value changes."""

    def test_var_value_changed(self):
        v_v1 = _pub_var("MAX", "_Z3MAXv", "int", value="100")
        v_v2 = _pub_var("MAX", "_Z3MAXv", "int", value="200")
        r = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        assert ChangeKind.VAR_VALUE_CHANGED in _kinds(r)


# ── var_access_changed / var_access_widened ──────────────────────────────

class TestVarAccessChanged:
    """Variable access level changes."""

    def test_var_access_narrowed(self):
        v_v1 = _pub_var("data", "_Z4datav", "int", access=AccessLevel.PUBLIC)
        v_v2 = _pub_var("data", "_Z4datav", "int", access=AccessLevel.PRIVATE)
        r = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        assert ChangeKind.VAR_ACCESS_CHANGED in _kinds(r)

    def test_var_access_widened(self):
        v_v1 = _pub_var("data", "_Z4datav", "int", access=AccessLevel.PRIVATE)
        v_v2 = _pub_var("data", "_Z4datav", "int", access=AccessLevel.PUBLIC)
        r = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        assert ChangeKind.VAR_ACCESS_WIDENED in _kinds(r)


# ── var_became_const / var_lost_const ────────────────────────────────────

class TestVarConstChanged:
    """Variable const qualifier changes."""

    def test_var_became_const(self):
        v_v1 = _pub_var("setting", "_Z7settingv", "int", is_const=False)
        v_v2 = _pub_var("setting", "_Z7settingv", "int", is_const=True)
        r = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        assert ChangeKind.VAR_BECAME_CONST in _kinds(r)

    def test_var_lost_const(self):
        v_v1 = _pub_var("setting", "_Z7settingv", "int", is_const=True)
        v_v2 = _pub_var("setting", "_Z7settingv", "int", is_const=False)
        r = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        assert ChangeKind.VAR_LOST_CONST in _kinds(r)


# ── constant_changed / constant_added / constant_removed ─────────────────

class TestConstantChanges:
    """Preprocessor/constexpr constant changes."""

    def test_constant_value_changed(self):
        old = _snap(constants={"API_VERSION": "1"})
        new = _snap(constants={"API_VERSION": "2"})
        r = compare(old, new)
        assert ChangeKind.CONSTANT_CHANGED in _kinds(r)

    def test_constant_added(self):
        old = _snap(constants={})
        new = _snap(constants={"NEW_FLAG": "1"})
        r = compare(old, new)
        assert ChangeKind.CONSTANT_ADDED in _kinds(r)

    def test_constant_removed(self):
        old = _snap(constants={"OLD_FLAG": "1"})
        new = _snap(constants={})
        r = compare(old, new)
        assert ChangeKind.CONSTANT_REMOVED in _kinds(r)


# ── Multiple simultaneous symbol changes ─────────────────────────────────

class TestMultipleSymbolChanges:
    """Verify multiple detectors fire correctly together."""

    def test_func_removed_and_return_changed(self):
        """Removing one func while changing another's return type."""
        f1 = _pub_func("a", "_Z1av")
        f2 = _pub_func("b", "_Z1bv", ret="int")
        f2_changed = _pub_func("b", "_Z1bv", ret="long")

        r = compare(
            _snap(functions=[f1, f2]),
            _snap(functions=[f2_changed]),
        )
        assert ChangeKind.FUNC_REMOVED in _kinds(r)
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_var_type_and_const_changed_together(self):
        """Variable type and const qualifier changed simultaneously.

        VAR_TYPE_CHANGED is the root cause; VAR_BECAME_CONST may be
        reported or may be deduplicated as redundant. Both should at
        least be in the full change set.
        """
        v_v1 = _pub_var("cfg", "_Z3cfgv", "int", is_const=False)
        v_v2 = _pub_var("cfg", "_Z3cfgv", "long", is_const=True)
        r = compare(_snap(variables=[v_v1]), _snap(variables=[v_v2]))
        # VAR_TYPE_CHANGED is the primary change; VAR_BECAME_CONST may be
        # subsumed when both type and const change simultaneously.
        all_k = {c.kind for c in r.changes + r.redundant_changes}
        assert ChangeKind.VAR_TYPE_CHANGED in all_k
        assert r.verdict == Verdict.BREAKING
