# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contract tests for :mod:`abicheck.model.yaml_strict`, the one strict
manifest loader ``dump_manifest``/``compatibility_evaluation_packs``/
``impact.use_cases``/``bundle_facts_library_overrides`` share.

Written as *invariants over generated documents* rather than a handful of
fixed strings, per AGENTS.md's "Primitive-level property tests" rule: this
is a reusable, general-purpose parsing primitive, and the three private
copies it replaced diverged precisely in cases nobody had written a fixed
example for (an unhashable key raised a raw ``TypeError`` out of one of
them; an out-of-range implicit scalar escaped another's documented error
type). The oracle for acceptance is ``yaml.safe_load`` — a genuinely
independent second implementation, not the function under test — and the
oracle for rejection is a *constructed* duplicate/unhashable key, not a
re-derivation of the loader's own check.
"""

from __future__ import annotations

import pytest
import yaml
from hypothesis import given, settings, strategies as st

from abicheck.model.yaml_strict import StrictMappingError, load_strict_yaml


class _Boom(Exception):
    """The domain error type a caller supplies via ``error=``."""


def _load(text: str) -> object:
    return load_strict_yaml(text, error=_Boom)


# --- Acceptance: identical to yaml.safe_load for any duplicate-free document


_SCALARS = st.one_of(
    st.integers(),
    st.booleans(),
    st.text(alphabet="abcxyz-_ ", min_size=0, max_size=8),
    st.none(),
)

_KEYS = st.text(alphabet="abcdefgh", min_size=1, max_size=4)

_DOCS = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(_KEYS, children, max_size=4),
    ),
    max_leaves=12,
)


@settings(max_examples=200, deadline=None)
@given(document=_DOCS)
def test_accepts_and_decodes_every_duplicate_free_document_like_safe_load(
    document: object,
) -> None:
    """The strict loader narrows *which documents are accepted* only by the
    duplicate/unhashable-key rule. For everything else it must be
    ``yaml.safe_load`` — same acceptance, same decoded value.

    ``yaml.safe_dump`` never emits a repeated key, so every document this
    generates is in the accepted set by construction; the oracle is PyYAML
    itself rather than a second call into the code under test.
    """
    text = yaml.safe_dump(document, default_flow_style=False, sort_keys=False)
    assert _load(text) == yaml.safe_load(text)


# --- Rejection: a duplicate key anywhere, at any nesting depth


@settings(max_examples=100, deadline=None)
@given(
    depth=st.integers(min_value=0, max_value=4),
    key=_KEYS,
)
def test_rejects_a_duplicate_key_at_any_nesting_depth(depth: int, key: str) -> None:
    """A repeat is an error wherever it appears — not only at the top level,
    which is the one place a fixed-example test would have pinned.

    The document is built by nesting the offending mapping under *depth*
    enclosing mappings, and the control assertion below proves the same
    document without the repeat parses fine, so the failure can only be
    attributable to the duplicate key itself.
    """
    prefix = "".join(f"{'  ' * i}k{i}:\n" for i in range(depth))
    pad = "  " * depth
    duplicated = f"{prefix}{pad}{key}: 1\n{pad}{key}: 2\n"
    clean = f"{prefix}{pad}{key}: 1\n{pad}{key}2: 2\n"

    assert _load(clean) is not None  # control: the shape itself is accepted
    with pytest.raises(_Boom, match="duplicate key"):
        _load(duplicated)
    # yaml.safe_load is the thing being *improved on*: it accepts it silently.
    assert yaml.safe_load(duplicated) is not None


@settings(max_examples=100, deadline=None)
@given(
    key=_KEYS,
    values=st.lists(st.integers(), min_size=2, max_size=5, unique=True),
)
def test_rejects_a_repeat_regardless_of_how_many_times_or_which_values(
    key: str, values: list[int]
) -> None:
    """Repetition count and value spelling never make a repeat acceptable —
    an invariant the "two identical lines" example cannot state."""
    text = "".join(f"{key}: {value}\n" for value in values)
    with pytest.raises(_Boom, match="duplicate key"):
        _load(text)


def test_a_repeat_across_two_sibling_mappings_is_not_a_duplicate() -> None:
    """The rule is *within one mapping*, not document-wide: the same key
    under two different parents is ordinary, valid YAML. Without this the
    invariant above could be satisfied by a loader that rejects far too
    much."""
    assert _load("a:\n  k: 1\nb:\n  k: 2\n") == {"a": {"k": 1}, "b": {"k": 2}}


# --- Rejection: an unhashable key, the divergence between the old copies


@pytest.mark.parametrize(
    "key_text",
    ["[a, b]", "{a: 1}", "[[a]]", "[]", "{}"],
    ids=["sequence", "mapping", "nested-sequence", "empty-sequence", "empty-mapping"],
)
def test_rejects_every_shape_of_unhashable_key(key_text: str) -> None:
    """A non-scalar mapping key is a schema violation reported through the
    caller's error type — never a raw ``TypeError``, which is what one of
    the three replaced copies leaked (``key in seen`` on a list).

    Enumerated over the whole small domain of unhashable YAML node shapes
    rather than the one ``? [a, b]`` case that was originally reported.
    """
    with pytest.raises(_Boom, match="unhashable mapping key"):
        _load(f"? {key_text}\n: 1\n")


# --- Every malformed-document failure reaches the caller as *their* type


@pytest.mark.parametrize(
    "text",
    [
        "a: [1, 2\n",  # syntax error
        "\ta: 1\n",  # tab indentation
        "a: 1\na: 2\n",  # duplicate key
        "? [a, b]\n: 1\n",  # unhashable key
        "a: 2023-99-99\n",  # implicit timestamp, out of range (bare ValueError)
        "a: 2023-01-01T99:99:99\n",  # implicit timestamp, out-of-range time
        "a: !!python/object:os.system {}\n",  # unsafe tag: SafeLoader rejects
        "*missing\n",  # undefined alias
    ],
    ids=[
        "syntax",
        "tab-indent",
        "duplicate-key",
        "unhashable-key",
        "bad-date",
        "bad-time",
        "unsafe-tag",
        "undefined-alias",
    ],
)
def test_no_malformed_document_escapes_as_a_foreign_exception_type(text: str) -> None:
    """The single promise every caller relies on: one error type out, for
    every class of malformed input — including the two that are *not*
    ``yaml.YAMLError`` subclasses (a duplicate/unhashable key and PyYAML's
    own implicit-resolver ``ValueError``), which is exactly where the three
    private copies disagreed with each other."""
    with pytest.raises(_Boom):
        _load(text)


def test_the_error_factory_receives_a_message_and_its_result_is_raised() -> None:
    """``error`` is a factory, not an exception class: the loader must call
    it with a human-readable message and raise what it returns, so a caller
    can prefix the message with its own source/path context."""
    seen: list[str] = []

    def factory(message: str) -> Exception:
        seen.append(message)
        return _Boom(f"prefix: {message}")

    with pytest.raises(_Boom, match=r"prefix: duplicate key 'a' in the same mapping"):
        load_strict_yaml("a: 1\na: 2\n", error=factory)
    assert len(seen) == 1


def test_duplicate_and_unhashable_messages_name_the_offending_line() -> None:
    """The line number is what makes the message actionable in a 200-line
    manifest; assert it against a document whose offending line is
    deliberately not line 1."""
    with pytest.raises(_Boom, match=r"\(line 3\)"):
        _load("head: 0\na: 1\na: 2\n")
    with pytest.raises(_Boom, match=r"\(line 2\)"):
        _load("head: 0\n? [a]\n: 1\n")


def test_strict_mapping_error_is_not_a_yaml_error() -> None:
    """A vacuity guard on the translation layer itself: if
    ``StrictMappingError`` were a ``yaml.YAMLError`` subclass, the
    ``except StrictMappingError`` arm's precise one-line message would be
    indistinguishable from the generic "invalid YAML: ..." arm, and this
    module's message tests would pass for the wrong reason."""
    assert not issubclass(StrictMappingError, yaml.YAMLError)


def test_a_recursion_error_is_deliberately_not_translated() -> None:
    """A pathologically nested document exhausts Python's recursion limit
    inside PyYAML's recursive-descent parser. That is a resource limit, not
    a schema violation, and the caller that surfaces it to a user needs its
    own message — so it must pass through untranslated."""
    with pytest.raises(RecursionError):
        _load("[" * 3000 + "]" * 3000)
