"""Location-expression evaluator: malformed input has no value, only "unknown".

``DW_AT_data_member_location`` expressions with no exact offset (a stack
underflow, a truncated operand, an unsupported opcode) must evaluate to
``None``. Every expected value below is computed by hand, not by the
evaluator's own helpers.
"""

from __future__ import annotations

import pytest
from elftools.dwarf.dwarf_expr import DWARFExprOp

from abicheck.dwarf_utils import _evaluate_location_expr


class TestEvaluateLocationExprUnknowns:
    """Pinned values from before the fix are now unknown (see the commit message)."""

    def test_single_int(self) -> None:
        """A bare integer is an opcode, not a constant: 0x2a (DW_OP_ge) is unsupported."""
        assert _evaluate_location_expr([42]) is None

    def test_non_int_tuple_elements(self) -> None:
        """A tuple whose opcode is not an int has no exact offset."""
        assert _evaluate_location_expr([("foo", "bar")]) is None

    def test_non_int_non_tuple_item(self) -> None:
        """A non-opcode item (e.g. a string) has no exact offset."""
        assert _evaluate_location_expr(["not_an_opcode"]) is None

    def test_plus_with_base_and_one_operand(self) -> None:
        """DW_OP_plus with the implicit base and one pushed value is valid: 0 + 3."""
        assert _evaluate_location_expr([0x33, 0x22]) == 3

    def test_tuple_plus_insufficient_stack(self) -> None:
        """Tuple DW_OP_plus with only the implicit base on the stack is unevaluable.

        It was pinned as a value (0): a malformed expression then produced a
        plausible offset that could fabricate or hide a layout change.
        """
        assert _evaluate_location_expr([(0x22, 0)]) is None

    def test_unknown_tuple_opcode(self) -> None:
        """An unsupported tuple opcode has no exact offset (never skipped to 0)."""
        assert _evaluate_location_expr([(0xFF, 99)]) is None

    def test_unknown_raw_opcode(self) -> None:
        """An unsupported raw opcode has no exact offset (never a constant)."""
        # 0x01 is DW_OP_addr, which this evaluator does not support
        assert _evaluate_location_expr([0x01]) is None


# ── location-expression underflow class ────────────────────────────────────


def _expr_op(op: int, args: list[object]) -> DWARFExprOp:
    """A real pyelftools ``DWARFExprOp`` (a namedtuple), as the parser yields."""
    return DWARFExprOp(op=op, op_name=f"0x{op:02x}", args=args, offset=0)


# Stack-consuming ops the evaluator supports, with how many entries each pops
# (the implicit base entry counts). plus_uconst is unary and is only reachable
# with its operand; the base entry is always present, so its "underflow" is a
# missing operand. Each case is (label, expression-with-no-real-operand-left).
_UNDERFLOW_CASES = [
    pytest.param([0x22], id="raw-plus"),
    pytest.param([(0x22, 0)], id="tuple-plus"),
    pytest.param([(0x22, None)], id="tuple-plus-no-operand-slot"),
    pytest.param([_expr_op(0x22, [])], id="dwarfexprop-plus"),
    # Partial-value trap: lit1 leaves [base, 1]; the first plus consumes it,
    # the second plus then has one entry and must not return the partial 1.
    pytest.param([0x31, 0x22, 0x22], id="raw-plus-after-valid-plus"),
    pytest.param([(0x31, 0), (0x22, 0), (0x22, 0)], id="tuple-plus-after-valid-plus"),
    pytest.param([0x23], id="raw-plus-uconst-missing-operand"),
    pytest.param([(0x23, None)], id="tuple-plus-uconst-missing-operand"),
    pytest.param([0x10], id="raw-constu-missing-operand"),
    pytest.param([0x23, 0x80], id="raw-plus-uconst-truncated-leb128"),
    pytest.param([0x11, 0xC0], id="raw-consts-truncated-sleb128"),
]


class TestLocationExprUnderflowClass:
    """Every stack-consuming op on an insufficient stack is unevaluable.

    Class oracle: the expected results below are hand-computed, not produced
    by the evaluator's own helpers. A value from an underflowing expression
    is a fabricated offset, so the only acceptable answer is ``None``.
    """

    @pytest.mark.parametrize("expr", _UNDERFLOW_CASES)
    def test_insufficient_stack_is_unknown(self, expr: list[object]) -> None:
        assert _evaluate_location_expr(expr) is None

    @pytest.mark.parametrize("opcode", [0x06, 0x1C, 0x24, 0x25, 0x2A, 0x51, 0xFF])
    def test_unsupported_opcode_is_unknown(self, opcode: int) -> None:
        """Unsupported ops (deref, minus, shl, ...) never fall through to a value."""
        assert _evaluate_location_expr([0x33, opcode]) is None
        assert _evaluate_location_expr([(opcode, 0)]) is None

    def test_exhaustive_single_raw_opcode(self) -> None:
        """Every single raw byte: only lit0..lit31 evaluate, to their literal value."""
        for byte in range(256):
            got = _evaluate_location_expr([byte])
            if 0x30 <= byte <= 0x4F:
                assert got == byte - 0x30, byte
            else:
                assert got is None, byte

    def test_exhaustive_two_op_sequences(self) -> None:
        """Sequences over a small alphabet agree with an independent oracle.

        The oracle is a deliberately naive stack machine: it returns None on
        any violation and never guesses. It shares no code with the evaluator.
        """
        alphabet: list[list[object]] = [
            [0x31],  # lit1
            [0x33],  # lit3
            [0x22],  # plus
            [0x23, 2],  # plus_uconst 2
            [0x10, 5],  # constu 5
            [0x06],  # deref (unsupported)
        ]

        def oracle(seq: list[object]) -> int | None:
            stack = [0]
            for item in seq:
                if item == 0x22:
                    if len(stack) < 2:
                        return None
                    stack[-1] = stack.pop() + stack[-1]
                elif item == 0x31:
                    stack.append(1)
                elif item == 0x33:
                    stack.append(3)
                elif item == 0x23:
                    stack[-1] += 2  # operand is consumed below
                elif item == 0x10:
                    stack.append(5)
                elif item == 2 or item == 5:
                    continue  # operand, consumed with its opcode
                else:
                    return None
            return stack[-1]

        for first in alphabet:
            for second in alphabet:
                expr: list[object] = first + second
                assert _evaluate_location_expr(expr) == oracle(expr), expr

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            pytest.param([0x23, 16], 16, id="raw-plus_uconst"),
            pytest.param([(0x23, 24)], 24, id="tuple-plus_uconst"),
            pytest.param([0x10, 10, 0x10, 20, 0x22], 30, id="raw-constu-constu-plus"),
            pytest.param([0x35, 0x23, 2], 7, id="lit5-plus_uconst-2"),
            # base 0; push 3, push 4, plus -> 7; plus_uconst 1 -> 8
            pytest.param([0x10, 3, 0x10, 4, 0x22, 0x23, 1], 8, id="nested-valid"),
            pytest.param([(0x10, 5), (0x10, 3), (0x22, 0)], 8, id="tuple-plus"),
            pytest.param([0x33, 0x22], 3, id="base-plus-one-operand"),
            pytest.param([], 0, id="empty-is-base"),
            # SLEB128 0x7F is -1 (sign bit 0x40 set); 0x80 0x7F is -128
            pytest.param([0x11, 0x7F, 0x22], -1, id="consts-negative-sign-extended"),
            pytest.param([0x11, 0x80, 0x7F, 0x22], -128, id="consts-two-byte-negative"),
        ],
    )
    def test_well_formed_controls(self, expr: list[object], expected: int) -> None:
        """Well-formed expressions still evaluate; expected values are by hand."""
        assert _evaluate_location_expr(expr) == expected
