"""Edit operators only L4 source replay or the L5 source graph sees.

Every edit here is made to the ``cxx_src`` seed and needs a ``sources``
config (``Config.sources``): the checker is given each side's source tree,
the way ``abicheck dump --sources`` takes it, and the harness's tree is
exactly the one it compiled. The replay facts the edits rely on (macros,
inline and template body fingerprints, call edges) come from a clang AST,
which abicheck's source replay runs only with the clang header backend
(castxml records no macros or bodies), so the edits needing them also need
``header_backend="clang"``.

Ground truth, from each edit's meaning:

* a removed public macro or uninstantiated template no longer compiles in a
  consumer: an API break, with nothing in the binary;
* a changed macro value or default argument is baked into consumers when
  they compile: the old binaries keep the old value, a rebuild silently takes
  the new one -- at least a risk, at most an API break, never an ABI break;
* a changed inline or template body ships only in consumers built from the
  new header: a risk, nothing in the library's binary interface moved;
* a public entry newly calling or depending on an internal function, or a
  virtual call gaining a dispatch target, changes no declaration a consumer
  sees: compatible, at most a risk;
* a public declaration added or removed moves the public surface (removal is
  the binary break it is), and a new export no header declares raises the
  undocumented-export share.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.config import Config
from abiforge.model.opkit import EditOp, Roles, _set
from abiforge.model.seeds import RoleText

__all__ = ["SOURCE_EVIDENCE_OPERATORS", "needs_sources"]


def needs_sources(config: Config) -> str | None:
    """Applicability: the cell must give the checker each side's source tree."""
    if config.sources:
        return None
    return "only source replay or the source graph sees it; needs a sources config"


def needs_clang_replay(config: Config) -> str | None:
    """Applicability: sources plus the clang header backend (replay bodies/macros)."""
    if config.sources and config.header_backend != "clang":
        return (
            "macros, bodies and call edges come from abicheck's clang source "
            "replay, which runs with the clang header backend only"
        )
    return needs_sources(config)


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

    kw.setdefault("precondition", needs_clang_replay)
    return EditOp(
        name,
        (role, "src.gate"),
        edit,
        why,
        expected=kinds,
        verdict_min=verdict_min,
        verdict_max=verdict_max,
        touches=(role,),
        min_evidence="headers",
        **kw,
    )


_VD_NEW_H = (
    "struct SrcBase { virtual ~SrcBase(); virtual int f(); };\n"
    "struct SrcLeft : SrcBase { int f(); };\n"
    "struct SrcRight : SrcBase { int f(); };\nint src_dispatch(SrcBase *b);"
)
_VD_NEW_C = (
    "SrcBase::~SrcBase() {}\nint SrcBase::f() { return 0; }\n"
    "int SrcLeft::f() { return 1; }\nint SrcRight::f() { return 2; }\n"
    "int src_dispatch(SrcBase *b) { return b->f(); }"
)

SOURCE_EVIDENCE_OPERATORS: tuple[EditOp, ...] = (
    _op(
        "src_macro_value_changed",
        "src.macro",
        "#define SRC_LIMIT 5",
        None,
        frozenset({"PUBLIC_MACRO_VALUE_CHANGED"}),
        "a public macro's value is baked into every consumer that compiles "
        "against it; the binary carries no trace of it",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
    ),
    _op(
        "src_macro_removed",
        "src.macro",
        "",
        None,
        frozenset({"PUBLIC_MACRO_REMOVED"}),
        "a consumer naming the macro no longer compiles",
        "API_BREAK",
        "API_BREAK",
    ),
    _op(
        "src_default_argument_changed",
        "src.dflt",
        "int src_take(int a, int b = 8);",
        None,
        frozenset({"DEFAULT_ARGUMENT_CHANGED"}),
        "a default argument is supplied by the caller at its compile time: old "
        "binaries keep passing 4, a rebuilt consumer passes 8",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
    ),
    _op(
        "src_inline_body_changed",
        "src.inl",
        "inline int src_twice(int v) { return v * 3; }",
        None,
        frozenset({"INLINE_BODY_CHANGED"}),
        "a header-only inline is compiled into each consumer: old and rebuilt "
        "consumers now disagree on its behavior, the library is unchanged",
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
    ),
    _op(
        "src_template_body_changed",
        "src.tmpl",
        "template <class T> T src_scale(T v) { return v * 3; }",
        None,
        frozenset({"TEMPLATE_BODY_CHANGED"}),
        "a header template is instantiated in each consumer: the body change "
        "reaches only rebuilt consumers",
        "COMPATIBLE_WITH_RISK",
        "COMPATIBLE_WITH_RISK",
    ),
    _op(
        "src_uninstantiated_template_removed",
        "src.tmpl",
        "",
        None,
        frozenset({"UNINSTANTIATED_TEMPLATE_REMOVED"}),
        "the library never instantiates the template, so nothing in the binary "
        "goes; a consumer instantiating it no longer compiles",
        "API_BREAK",
        "API_BREAK",
    ),
    _op(
        "src_public_entry_calls_internal",
        "src.call",
        None,
        "static int src_helper(int v) { return v; }\n"
        "int src_entry(int v) { return src_helper(v); }",
        frozenset({"CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED"}),
        "a public entry now reaches an internal function through a call edge; "
        "no declaration changed",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
    ),
    _op(
        "src_public_entry_depends_on_hidden",
        "src.call",
        None,
        '__attribute__((visibility("hidden"))) int src_inner(int v) '
        "{ return v; }\nint src_entry(int v) { return src_inner(v); }",
        frozenset(
            {"PUBLIC_API_INTERNAL_DEPENDENCY_ADDED", "PUBLIC_TO_INTERNAL_DEPENDENCY"}
        ),
        "a public entry now depends on a hidden-visibility internal function: "
        "the public API couples to an implementation detail, no declaration "
        "changed",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
    ),
    _op(
        "src_virtual_dispatch_target_added",
        "src.vd",
        _VD_NEW_H,
        _VD_NEW_C,
        frozenset({"PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED"}),
        "a new public subclass overrides SrcBase::f, so the public "
        "src_dispatch virtual call gains a dispatch target; existing layouts "
        "and vtables are unchanged",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
    ),
    _op(
        "src_public_function_added",
        "src.fn",
        "int src_count(const char *s);\nint src_plus(int v);",
        "int src_count(const char *s) { int n = 0; while (s[n]) ++n; return n; }\n"
        "int src_plus(int v) { return v + 1; }",
        frozenset({"FUNC_ADDED", "PUBLIC_SURFACE_GREW"}),
        "one public function is added: the public surface grows",
        "COMPATIBLE",
        "COMPATIBLE",
        precondition=needs_sources,
    ),
    _op(
        "src_public_function_removed",
        "src.extra",
        "",
        "",
        frozenset({"FUNC_REMOVED", "PUBLIC_SURFACE_SHRANK"}),
        "a public, exported function is removed: a binary break that also "
        "shrinks the public surface",
        "BREAKING",
        None,
        precondition=needs_sources,
    ),
    _op(
        "src_undocumented_export_added",
        "src.fn",
        None,
        "int src_count(const char *s) { int n = 0; while (s[n]) ++n; return n; }\n"
        "int src_undoc(int v) { return v + 3; }",
        frozenset({"UNDOCUMENTED_EXPORT_RATIO_INCREASED"}),
        "a new export no header declares: the share of undocumented exports "
        "rises; no public declaration changed",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
        precondition=needs_sources,
    ),
)
