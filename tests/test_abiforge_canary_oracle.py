"""The canary disassembly oracle, on captured disassembly text (no tool needed).

The integration test in ``test_abiforge_binary_licence.py`` runs the real
``objdump``. These fixtures pin the line matcher on the spellings each
disassembler prints, so a parsing gap on one platform is caught on Linux too.
"""

from __future__ import annotations

import pytest

from tests.test_abiforge_binary_licence import canary_call_lines

CALL_LINES = [
    # ELF x86-64 (GNU objdump): a call through the PLT.
    ("  401136:\tcall   401030 <__stack_chk_fail@plt>", True),
    # GNU objdump prints the instruction bytes before the mnemonic.
    ("  1139:\te8 f2 fe ff ff\tcall   1030 <__stack_chk_fail@plt>", True),
    # ELF AArch64: the branch-and-link spelling.
    ("  4005a4:\tbl     400430 <__stack_chk_fail@plt>", True),
    # Mach-O (llvm-objdump): a named stub target.
    ("100003f1c:\tbl\t0x100003f40 <___stack_chk_fail$stub>", True),
    # Mach-O: the stub annotation names the import when the target is unnamed.
    ("100003f1c:\tbl\t0x100003f40 ; symbol stub for: ___stack_chk_fail", True),
    # Not a call: the symbol is only referenced as data.
    ("  40113a:\tlea    0x0(%rip),%rdi        # 404000 <__stack_chk_fail>", False),
    # A call to another symbol, which must not count.
    ("  401136:\tcall   401030 <puts@plt>", False),
    # A canary load is not a check call.
    ("  401142:\tmov    %fs:0x28,%rax  ; __stack_chk_guard", False),
]


@pytest.mark.parametrize(("line", "is_call"), CALL_LINES)
def test_a_line_is_a_canary_call_only_when_it_calls_the_check(line, is_call) -> None:
    assert bool(canary_call_lines(line)) is is_call


def test_a_disassembly_with_one_canary_call_among_others_reports_that_line() -> None:
    text = "\n".join(
        [
            "0000000000401126 <shape>:",
            "  401130:\tcall   401030 <puts@plt>",
            "  401136:\tcall   401040 <__stack_chk_fail@plt>",
            "  401140:\tret",
        ]
    )
    assert canary_call_lines(text) == [
        "  401136:\tcall   401040 <__stack_chk_fail@plt>"
    ]
