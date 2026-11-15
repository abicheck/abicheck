"""Operator expectations that follow the platform's link rules.

``key_function_inlined``:

ELF: the inline ``VKy::f`` stays exported as a weak (vague-linkage) definition,
so an old consumer still binds and the edit is at most a risk. Mach-O: clang
marks the linkonce_odr unnamed_addr definition ``weak_def_can_be_hidden`` and
ld64 auto-hides it, so the dylib stops exporting a symbol old consumers bind to
by two-level name and dyld refuses to load them: a break owing a removal or
visibility finding (what the macOS integration job observed).

``object_became_weak`` / ``object_became_inline_variable`` on PE (MinGW): COFF
has no dynamic symbol binding. ld's automatic DLL export leaves a weak
definition out of the export table (a break), while an inline variable is a
COMDAT still exported under its name (nothing moves). On ELF both stay
exported with a changed binding, at most a risk.
"""

from __future__ import annotations

import pytest

from abiforge.model.config import Toolchain, default_config
from abiforge.operators import OP_BY_NAME

_GCC = Toolchain("gcc", 13, "gcc", "g++")
_CLANG = Toolchain("clang", 18, "clang", "clang++")

# Oracle: platform -> (verdict_min, verdict_max, owes a removal/visibility kind).
_ORACLE = {
    ("linux", "elf"): ("COMPATIBLE", "COMPATIBLE_WITH_RISK", False),
    ("darwin", "macho"): ("BREAKING", None, True),
}
_REMOVAL = frozenset({"FUNC_REMOVED", "FUNC_VISIBILITY_CHANGED"})
# The kinds the macOS job reported for this edit.
_OBSERVED_ON_MACHO = frozenset(
    {
        "EXPORTED_NOT_PUBLIC",
        "FUNC_BECAME_INLINE",
        "FUNC_REMOVED",
        "FUNC_VISIBILITY_CHANGED",
    }
)


@pytest.mark.parametrize("producer", [_GCC, _CLANG], ids=["gcc", "clang"])
@pytest.mark.parametrize("platform", sorted(_ORACLE), ids=str)
def test_key_function_inlined_expectation_per_platform(
    producer: Toolchain, platform: tuple[str, str]
) -> None:
    cfg = default_config("cxx", producer).with_(evidence="binary", platform=platform)
    exp = OP_BY_NAME["key_function_inlined"].expect(cfg)
    vmin, vmax, owes_removal = _ORACLE[platform]
    assert (exp.verdict_min, exp.verdict_max) == (vmin, vmax)
    assert any(group & _REMOVAL for group in exp.any_of) is owes_removal
    # A removal read as BREAKING passes on Mach-O and still fails on ELF.
    assert (exp.problems("BREAKING", _OBSERVED_ON_MACHO) == []) is owes_removal


# Oracle: (operator, objfmt) -> (verdict_min, verdict_max, owed kind group).
_VAR_ORACLE = {
    ("object_became_weak", "elf"): ("COMPATIBLE", "COMPATIBLE_WITH_RISK", None),
    ("object_became_weak", "pe"): (
        "BREAKING",
        None,
        frozenset({"VAR_VISIBILITY_CHANGED", "VAR_REMOVED", "VAR_REMOVED_ELF_ONLY"}),
    ),
    ("object_became_inline_variable", "elf"): (
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
        frozenset({"SYMBOL_BINDING_CHANGED", "SYMBOL_BINDING_BECAME_UNIQUE"}),
    ),
    ("object_became_inline_variable", "pe"): (
        "NO_CHANGE",
        "COMPATIBLE_WITH_RISK",
        None,
    ),
}
_PLATFORM = {"elf": ("linux", "elf"), "pe": ("win32", "pe")}


@pytest.mark.parametrize(("op_name", "objfmt"), sorted(_VAR_ORACLE))
def test_variable_binding_expectations_per_platform(op_name: str, objfmt: str) -> None:
    cfg = default_config("cxx", _GCC).with_(
        evidence="binary", platform=_PLATFORM[objfmt]
    )
    exp = OP_BY_NAME[op_name].expect(cfg)
    vmin, vmax, group = _VAR_ORACLE[(op_name, objfmt)]
    assert (exp.verdict_min, exp.verdict_max) == (vmin, vmax)
    assert (group in exp.any_of) if group else not exp.any_of
    if objfmt == "pe":
        assert "SYMBOL_BINDING_CHANGED" not in exp.expected
