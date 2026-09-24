"""Contract of the template-name primitives the internal-template detector
groups instantiations by.

Two bug classes, both found on a real report:

* a ``(`` inside a template argument -- clang's ``(lambda at h.hpp:1:2)`` or
  ``(unnamed struct at ...)`` spelling, a function-type argument
  ``F<int(double)>`` -- was taken for the declaration's parameter list,
  truncating ``foldable<(lambda ...)>::f`` to ``foldable<`` so every
  lambda-parameterised instantiation fell out of the check entirely;
* an operator name's own ``<``/``>`` (``operator<<``, ``operator->``,
  ``operator<=>``) was read as a template bracket, collapsing
  ``C<int>::operator<<`` and ``C<int>::operator<`` onto one stem.

Names are generated from a small grammar and the expected stem / callable
name is assembled *alongside* each generated name, so the oracle never
re-parses a string the way the implementation does.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.compare.template_surface import strip_template_args, template_angle_depth
from abicheck.diff_templates import (
    _looks_like_template_instantiation,
    _strip_param_signature,
)

_IDENT = st.sampled_from(["lib", "detail", "kumi", "foldable", "run", "C", "x_1"])
_OPERATORS = [
    "operator<<",
    "operator<",
    "operator>>",
    "operator<=>",
    "operator->",
    "operator->*",
    "operator<=",
    "operator()",
    "operator+",
]
_LEAF_ARGS = st.sampled_from(
    [
        "int",
        "const unsigned char &",
        "(lambda at /w/old/include/kumi.hpp:1138:31)",
        "(lambda:kumi.hpp:12:3)",
        "(unnamed struct at a b/h.h:5:6)",
        "int (double)",
        "void (*)(int)",
        "std::pair<int, (lambda at x.h:1:2)>",
    ]
)


@st.composite
def _template_args(draw: st.DrawFn) -> str:
    args = draw(st.lists(_LEAF_ARGS, min_size=1, max_size=3))
    return "<" + ", ".join(args) + ">"


@st.composite
def _declaration(draw: st.DrawFn) -> tuple[str, str, str, bool]:
    """(declared name + params, callable name, stem, is_template)."""
    segments = draw(st.lists(_IDENT, min_size=1, max_size=3))
    templated = draw(
        st.lists(st.booleans(), min_size=len(segments), max_size=len(segments))
    )
    leaf = draw(st.sampled_from([*_OPERATORS, "apply"]))
    leaf_args = draw(st.one_of(st.just(""), _template_args()))
    callable_parts, stem_parts = [], []
    for seg, t in zip(segments, templated):
        args = draw(_template_args()) if t else ""
        callable_parts.append(seg + args)
        stem_parts.append(seg)
    sep = " " if leaf_args and leaf.endswith(("<", ">")) else ""
    callable_parts.append(leaf + sep + leaf_args)
    stem_parts.append(leaf)
    callable_name = "::".join(callable_parts)
    params = draw(
        st.sampled_from(["", "(int)", "(const T &, (lambda at q.h:9:9))", "() const"])
    )
    is_template = any(templated) or bool(leaf_args)
    return callable_name + params, callable_name, "::".join(stem_parts), is_template


@settings(max_examples=400, deadline=None)
@given(_declaration())
def test_param_signature_never_cut_inside_template_arguments(decl) -> None:
    full, callable_name, _, _ = decl
    assert _strip_param_signature(full) == callable_name


@settings(max_examples=400, deadline=None)
@given(_declaration())
def test_stem_keeps_operator_names_and_drops_every_argument_list(decl) -> None:
    _, callable_name, stem, _ = decl
    assert strip_template_args(callable_name) == stem


@settings(max_examples=400, deadline=None)
@given(_declaration())
def test_template_detection_ignores_operator_brackets(decl) -> None:
    _, callable_name, _, is_template = decl
    assert _looks_like_template_instantiation(callable_name) is is_template


@settings(max_examples=200, deadline=None)
@given(_declaration())
def test_depth_is_zero_after_a_complete_name(decl) -> None:
    _, callable_name, _, _ = decl
    assert template_angle_depth(callable_name) == 0
    # ...and positive at every `(` a template argument opens.
    for i, ch in enumerate(callable_name):
        if (
            ch == "("
            and i
            and callable_name[i - 1] in "<, "
            and "at " in callable_name[i : i + 12]
        ):
            assert template_angle_depth(callable_name[:i]) > 0


def test_distinct_operators_stay_distinct_stems() -> None:
    stems = {strip_template_args(f"lib::C<int>::{op}") for op in _OPERATORS}
    assert len(stems) == len(_OPERATORS)
