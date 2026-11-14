"""Edit operators only a multi-file, multi-TU source tree shows.

Every edit here is made to the ``cxx_tree`` seed (a public header, a private
header it includes, two translation units) and needs a ``sources`` config
with the clang header backend, like ``abiforge.operators.source_evidence``:
the facts come from L4 source replay and the L5 graph over each side's tree.

Ground truth, from each edit's meaning:

* a private (non-installed) polymorphic class whose typeinfo and vtable the
  library now exports, or a function declared only in a private header that
  the library now exports, adds nothing a consumer's header can name: the
  library grows an undocumented binary surface -- a risk, never a break;
* a public type whose definition now differs between the library's two
  translation units (a macro one of them defines) is an ODR violation: the
  library's own code disagrees on the layout, which a consumer can hit --
  at least a risk, at most an API break (nothing a consumer built against
  the old library calls moved);
* an anonymous type that only moves down its header keeps its definition:
  only its source coordinates change -- compatible.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.opkit import EditOp, Roles, _set
from abiforge.model.seeds import RoleText
from abiforge.operators.source_evidence import needs_clang_replay

__all__ = ["SOURCE_TREE_OPERATORS"]


def _op(
    name: str,
    role: str,
    header: str | None,
    source: str | None,
    kinds: frozenset[str],
    why: str,
    verdict_min: str,
    verdict_max: str | None,
    **kw: Any,
) -> EditOp:
    def edit(r: Roles) -> dict[str, RoleText]:
        return _set(r, role, header, source)

    return EditOp(
        name,
        (role, "tree.gate"),
        edit,
        why,
        expected=kinds,
        verdict_min=verdict_min,
        verdict_max=verdict_max,
        touches=(role,),
        min_evidence="headers",
        precondition=needs_clang_replay,
        **kw,
    )


SOURCE_TREE_OPERATORS: tuple[EditOp, ...] = (
    _op(
        "tree_internal_rtti_exported",
        "tree.rtti",
        "namespace tree_detail { struct Impl { virtual ~Impl(); virtual int f(); }; }",
        "namespace tree_detail { Impl::~Impl() {} int Impl::f() { return 1; } }",
        frozenset({"RTTI_FOR_INTERNAL_TYPE"}),
        "a polymorphic class declared only in the private header gets an "
        "out-of-line definition: the library exports its typeinfo and vtable, "
        "a surface no installed header declares",
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
    ),
    _op(
        "tree_private_declaration_exported",
        "tree.leak",
        "int tree_hidden_helper(int v);",
        "int tree_hidden_helper(int v) { return v; }",
        frozenset(
            {"SOURCE_DECL_BINARY_SYMBOL_MISMATCH", "SOURCE_TO_BINARY_MAPPING_CHANGED"}
        ),
        "a function declared only in the private header is defined and "
        "exported: the source declaration and the binary export disagree on "
        "whether it is public API",
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
    ),
    _op(
        "tree_type_definition_diverges_per_tu",
        "tree.pair",
        "#ifdef EDIT_UNIT2\n#define TREE_TAIL_T long long\n#else\n"
        "#define TREE_TAIL_T int\n#endif\n"
        "struct TreePair { int a; TREE_TAIL_T tail; };\n"
        "int tree_pair_a(const TreePair *p);\nint tree_pair_b(const TreePair *p);",
        None,
        frozenset({"ODR_SOURCE_CONFLICT", "ODR_TYPE_VARIANT"}),
        "TreePair's trailing member widens only where EDIT_UNIT2 is defined, "
        "which the second translation unit does: the library's two units "
        "define one type two ways (an ODR violation); the leading member "
        "both read is unchanged, and a consumer sees the old layout",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        # A per-TU macro: the construct is the compile configuration, not
        # one record edit.
        constructs=("compile_option",),
    ),
    _op(
        "tree_anonymous_type_coordinates_shifted",
        "tree.anon",
        "\n\n\nstruct TreeHolder { struct { int a; } in; int n; };\n"
        "int tree_holder_n(const TreeHolder *h);",
        None,
        frozenset({"DECLARATION_COORDINATES_SHIFTED"}),
        "the anonymous member type moves three lines down its header; an "
        "anonymous type is named by its location, so only its coordinates "
        "change, never its definition",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
    ),
)
