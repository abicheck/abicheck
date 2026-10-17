# DWARF member-location evaluator implements only a subset of DW_OP

**Status (2026-10-17): deliberate limitation, recorded with the fix that made it visible.**

`abicheck/dwarf_utils.py` evaluates `DW_AT_data_member_location` expressions with a small stack machine. It implements `DW_OP_plus`, `DW_OP_plus_uconst`, `DW_OP_constu`, `DW_OP_consts` and `DW_OP_lit0..31`. Before this change, any other opcode was either skipped or pushed as a constant, so the member offset silently came out wrong. Now a member location that uses an unsupported opcode, or an underflowing, truncated or otherwise malformed expression, yields "unknown" (`None`). The member's field offset is then unknown, and the enclosing record's layout is not recorded when that offset is needed.

What is not done: the unsupported operations (`DW_OP_minus`, `DW_OP_mul`, `DW_OP_deref`, `DW_OP_shl`, and the rest) are not implemented. Mainstream compilers do not put them in `data_member_location`, so the practical effect is limited to unusual toolchains. Layouts that depend on them lose field-offset evidence, which lowers assurance and never produces a clean result or a fabricated break.

Implementing the remaining operations would be a feature, not a fix. It needs a real fixture that emits them and an oracle that is not this evaluator.
