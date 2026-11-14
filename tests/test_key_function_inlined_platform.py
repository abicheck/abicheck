"""``key_function_inlined``'s expected verdict follows the platform's link rules.

ELF: the inline ``VKy::f`` stays exported as a weak (vague-linkage) definition,
so an old consumer still binds and the edit is at most a risk. Mach-O: clang
marks the linkonce_odr unnamed_addr definition ``weak_def_can_be_hidden`` and
ld64 auto-hides it, so the dylib stops exporting a symbol old consumers bind to
by two-level name and dyld refuses to load them: a break owing a removal or
visibility finding (what the macOS integration job observed).
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
