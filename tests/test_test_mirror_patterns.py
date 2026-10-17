# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""``scripts/test_mirror_patterns.py``: each mirror rule fires on its shape and
stays quiet on the near-miss that is a real test.

The near-misses are the point. A rule that cannot tell ``isinstance`` plus a
value check from ``isinstance`` alone, or an echo that also calls the code under
test from a bare echo, would bury real tests in the baseline.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from test_mirror_patterns import (  # noqa: E402
    compare_to_baseline,
    load_baseline,
    rule_counts,
    scan_source,
    scan_tree,
)

PATH = "tests/test_x.py"


def _rules(src: str) -> list[str]:
    return [f.rule for f in scan_source(textwrap.dedent(src), PATH)]


# ---------------------------------------------------------------------------
# type-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "def test_a():\n    assert isinstance(make(), Foo)\n",
        "def test_a():\n    assert issubclass(Foo, Base)\n",
        "def test_a():\n    assert hasattr(make(), 'x')\n",
        "def test_a():\n    assert callable(make)\n",
        "def test_a():\n    assert type(make()) is Foo\n",
        "def test_a():\n    assert make() is not None\n",
        "def test_a():\n    assert make() is None\n",
        "def test_a():\n    assert len(make()) > 0\n",
        "def test_a():\n    assert len(make()) >= 1\n",
        "def test_a():\n    x = make()\n    assert x\n",
        "def test_a():\n    assert isinstance(make(), Foo) and callable(make)\n",
        "def test_a():\n    assert not isinstance(make(), int)\n",
    ],
)
def test_type_only_fires_on_presence_and_type_checks(src: str) -> None:
    assert _rules(src) == ["type-only"]


@pytest.mark.parametrize(
    "src",
    [
        # a value check alongside the type check: the test verifies behaviour
        "def test_a():\n    r = make()\n    assert isinstance(r, Foo)\n    assert r.n == 3\n",
        "def test_a():\n    assert isinstance(make(), Foo) and make().n == 3\n",
        # size equality is a value, not presence
        "def test_a():\n    assert len(make()) == 3\n",
        # a value compare that reads like a presence check
        "def test_a():\n    assert make().name == 'a'\n",
        # no assertion at all is test-assertion-density's job, not this rule's
        "def test_a():\n    make()\n",
        # an opaque check (pytest.raises) is a behaviour check
        "import pytest\ndef test_a():\n    with pytest.raises(ValueError):\n        make()\n",
    ],
)
def test_type_only_does_not_fire_when_a_value_is_checked(src: str) -> None:
    assert "type-only" not in _rules(src)


# ---------------------------------------------------------------------------
# constructor-echo
# ---------------------------------------------------------------------------


def test_constructor_echo_fires_on_bare_readback() -> None:
    src = """
    def test_a():
        x = Cls(name="a", size=3)
        assert x.name == "a"
    """
    assert _rules(src) == ["constructor-echo"]


def test_constructor_echo_fires_when_the_argument_is_a_name() -> None:
    src = """
    def test_a():
        label = "a"
        x = Cls(name=label)
        assert x.name == label
    """
    assert _rules(src) == ["constructor-echo"]


def test_constructor_echo_fires_alongside_a_type_check() -> None:
    src = """
    def test_a():
        x = Cls(name="a")
        assert isinstance(x, Cls)
        assert x.name == "a"
    """
    assert _rules(src) == ["constructor-echo"]


@pytest.mark.parametrize(
    "src",
    [
        # echo plus a call of the method under test: a real test
        """
        def test_a():
            x = Cls(name="a")
            assert x.render() == "a"
            assert x.name == "a"
        """,
        # echo plus an unrelated function call: still code under test
        """
        def test_a():
            x = Cls(name="a")
            y = compute(x)
            assert x.name == "a"
        """,
        # the readback value differs from the constructor argument: not an echo
        """
        def test_a():
            x = Cls(name="a")
            assert x.name == "b"
        """,
        # an attribute the constructor did not receive is derived: not an echo
        """
        def test_a():
            x = Cls(name="a")
            assert x.size == 3
        """,
        # a second assertion checks something the constructor did not set
        """
        def test_a():
            x = Cls(name="a")
            assert x.name == "a"
            assert x.size == 3
        """,
    ],
)
def test_constructor_echo_does_not_fire_on_a_real_check(src: str) -> None:
    assert "constructor-echo" not in _rules(src)


# ---------------------------------------------------------------------------
# constant-mirror
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        'from x import ChangeKind\ndef test_a():\n    assert ChangeKind.FOO.value == "foo"\n',
        "import mod\ndef test_a():\n    assert mod.CONST == 3\n",
        "from x import Enum\ndef test_a():\n    assert Enum.A.B == 1\n",
    ],
)
def test_constant_mirror_fires_on_a_module_constant_against_a_literal(src: str) -> None:
    assert _rules(src) == ["constant-mirror"]


@pytest.mark.parametrize(
    "src",
    [
        # calls the code under test
        'from x import ChangeKind\ndef test_a():\n    assert ChangeKind.FOO.value == "foo"\n    assert classify(FOO) == 1\n',
        # the root is a local, not a module constant
        "def test_a(cfg):\n    assert cfg.timeout == 3\n",
        # compared to another name, not a literal
        "import mod\ndef test_a(other):\n    assert mod.CONST == other\n",
        # ordering comparison, not equality
        "import mod\ndef test_a():\n    assert mod.CONST > 3\n",
    ],
)
def test_constant_mirror_does_not_fire_on_a_real_check(src: str) -> None:
    assert "constant-mirror" not in _rules(src)


# ---------------------------------------------------------------------------
# mock-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        """
        from unittest import mock
        def test_a():
            with mock.patch("x.save") as save:
                run()
            save.assert_called_once()
        """,
        """
        from unittest.mock import MagicMock
        def test_a():
            m = MagicMock()
            run(m)
            m.assert_called_once_with(1)
        """,
        """
        def test_a(monkeypatch):
            spy = []
            monkeypatch.setattr("x.save", spy.append)
            run()
            assert spy.call_count == 1
        """,
    ],
)
def test_mock_only_fires_when_only_mock_bookkeeping_is_asserted(src: str) -> None:
    assert _rules(src) == ["mock-only"]


@pytest.mark.parametrize(
    "src",
    [
        # the real return value is checked as well
        """
        from unittest import mock
        def test_a():
            with mock.patch("x.save") as save:
                result = run()
            save.assert_called_once()
            assert result == 3
        """,
        # a mock assertion but nothing was patched: not the wiring-only shape
        """
        def test_a(save):
            run(save)
            save.assert_called_once()
        """,
        # a patch, but the test asserts only the real state
        """
        from unittest import mock
        def test_a():
            with mock.patch("x.save"):
                assert run() == 3
        """,
    ],
)
def test_mock_only_does_not_fire_when_a_real_value_is_asserted(src: str) -> None:
    assert "mock-only" not in _rules(src)


# ---------------------------------------------------------------------------
# Helper resolution, pragma, and opacity
# ---------------------------------------------------------------------------


def test_a_same_file_helper_that_checks_a_value_clears_the_test() -> None:
    src = """
    def _check_result(r):
        assert r.n == 3

    def test_a():
        r = make()
        assert isinstance(r, Foo)
        _check_result(r)
    """
    assert _rules(src) == []


def test_a_same_file_helper_that_is_only_a_type_check_still_flags() -> None:
    src = """
    def _check_type(r):
        assert isinstance(r, Foo)

    def test_a():
        _check_type(make())
    """
    assert _rules(src) == ["type-only"]


def test_a_helper_that_calls_the_code_under_test_blocks_the_echo_rule() -> None:
    src = """
    def _render(x):
        return x.render()

    def test_a():
        x = Cls(name="a")
        _render(x)
        assert x.name == "a"
    """
    assert "constructor-echo" not in _rules(src)


def test_a_mirror_pragma_on_the_def_line_exempts_the_test() -> None:
    src = """
    def test_a():  # mirror-ok: the type is the contract for this adapter
        assert isinstance(make(), Foo)
    """
    assert _rules(src) == []


def test_a_mirror_pragma_without_a_reason_does_not_exempt() -> None:
    src = """
    def test_a():  # mirror-ok:
        assert isinstance(make(), Foo)
    """
    assert _rules(src) == ["type-only"]


def test_a_pragma_on_a_body_line_does_not_exempt() -> None:
    src = """
    def test_a():
        assert isinstance(make(), Foo)  # mirror-ok: a reason on the wrong line
    """
    assert _rules(src) == ["type-only"]


def test_an_opaque_assertion_call_is_never_flagged() -> None:
    src = """
    def test_a():
        assert_roundtrip(make())
    """
    assert _rules(src) == []


def test_one_rule_per_test() -> None:
    src = """
    def test_a():
        x = Cls(name="a")
        assert isinstance(x, Cls)
        assert x.name == "a"
    """
    assert len(scan_source(textwrap.dedent(src), PATH)) == 1


# ---------------------------------------------------------------------------
# Ratchet
# ---------------------------------------------------------------------------


def _finding(test: str, rule: str = "type-only", line: int = 1):
    from test_mirror_patterns import Finding

    return Finding(PATH, test, line, rule)


def test_a_new_finding_outside_the_baseline_is_an_error() -> None:
    errors = compare_to_baseline([_finding("test_a")], {})
    assert len(errors) == 1
    assert "test_a" in errors[0]


def test_a_finding_at_its_baseline_count_is_clean() -> None:
    key = f"{PATH}::test_a::type-only"
    assert compare_to_baseline([_finding("test_a")], {key: 1}) == []


def test_a_baselined_finding_that_disappears_is_an_error_until_shrunk() -> None:
    key = f"{PATH}::test_a::type-only"
    errors = compare_to_baseline([], {key: 1})
    assert len(errors) == 1
    assert "--update" in errors[0]


def test_counts_are_per_key_not_per_line() -> None:
    findings = [_finding("test_a", line=10), _finding("test_a", line=90)]
    key = f"{PATH}::test_a::type-only"
    assert compare_to_baseline(findings, {key: 2}) == []
    assert len(compare_to_baseline(findings, {key: 1})) == 1


def test_rule_counts_cover_every_rule() -> None:
    counts = rule_counts([_finding("test_a", "mock-only")])
    assert counts == {
        "type-only": 0,
        "constructor-echo": 0,
        "constant-mirror": 0,
        "mock-only": 1,
    }


# ---------------------------------------------------------------------------
# Live tree
# ---------------------------------------------------------------------------


@pytest.mark.repo_scan
def test_live_tree_matches_the_recorded_baseline() -> None:
    errors = compare_to_baseline(list(scan_tree()), load_baseline())
    assert errors == [], "\n".join(errors)
