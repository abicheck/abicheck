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

"""Unit tests for ``scripts/audit_test_quality.py``.

Each flag has a positive snippet and at least one negative snippet that looks
similar but must not be flagged. Snippets go through ``audit_source`` and the
flags are read back by test name, so the tests exercise the real scanner.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from audit_test_quality import (  # noqa: E402
    FLAGS,
    GATED_FLAGS,
    audit_source,
    audit_tree,
    evaluate,
    gated_findings,
    main,
    parse_baseline,
)


def _flags(src: str) -> dict[str, tuple[str, ...]]:
    """Map each test's qualified name to its flags for a snippet."""
    return {r.test: r.flags for r in audit_source(textwrap.dedent(src))}


def _only(src: str) -> tuple[str, ...]:
    """Flags of the single test in *src*."""
    result = _flags(src)
    assert len(result) == 1, result
    return next(iter(result.values()))


# ---------------------------------------------------------------------------
# type_only
# ---------------------------------------------------------------------------


def test_type_only_flags_existence_and_type_checks() -> None:
    flags = _only(
        """
        def test_build():
            obj = build()
            assert isinstance(obj, dict)
            assert obj is not None
        """
    )
    assert "type_only" in flags


@pytest.mark.parametrize(
    "assertion",
    [
        "assert len(x) > 0",
        "assert len(x) >= 1",
        "assert callable(x)",
        "assert hasattr(x, 'name')",
        "assert x is not None",
        "assert isinstance(x, (list, tuple))",
    ],
)
def test_type_only_accepts_each_weak_form(assertion: str) -> None:
    flags = _only(
        f"""
        def test_weak():
            x = build()
            {assertion}
        """
    )
    assert "type_only" in flags


@pytest.mark.parametrize(
    "assertion",
    [
        # A flag, an empty collection, an absent value, an absent attribute
        # and the class a dispatcher picked are all outcomes code can get
        # wrong, so none of them is "only a type check".
        "assert x",
        "assert x.ready",
        "assert not x.offenders",
        "assert x is None",
        "assert not hasattr(x, 'legacy')",
        "assert isinstance(x, RpmExtractor)",
        "assert isinstance(x, (dict, Widget))",
    ],
)
def test_type_only_rejects_each_behavioural_form(assertion: str) -> None:
    flags = _only(
        f"""
        def test_behaviour():
            x = build()
            {assertion}
        """
    )
    assert "type_only" not in flags


def test_type_only_negative_when_one_assert_compares_a_value() -> None:
    flags = _only(
        """
        def test_build():
            obj = build()
            assert isinstance(obj, Widget)
            assert obj.version == "2.0"
        """
    )
    assert "type_only" not in flags


def test_type_only_negative_when_expected_exception_is_asserted() -> None:
    flags = _only(
        """
        import pytest

        def test_build():
            obj = build()
            assert isinstance(obj, Widget)
            with pytest.raises(ValueError):
                obj.fail()
        """
    )
    assert "type_only" not in flags


def test_type_only_negative_without_any_assert() -> None:
    flags = _only(
        """
        def test_smoke():
            build()
        """
    )
    assert "type_only" not in flags


# ---------------------------------------------------------------------------
# mock_echo
# ---------------------------------------------------------------------------


def test_mock_echo_flags_patch_then_only_call_verification() -> None:
    flags = _only(
        """
        def test_wiring(monkeypatch):
            fake = FakeHelper()
            monkeypatch.setattr(module, "helper", fake)
            run()
            fake.assert_called_once()
        """
    )
    assert "mock_echo" in flags


@pytest.mark.parametrize(
    "verification",
    [
        "fake.assert_called_once_with('gcc')",
        "assert fake.call_args.args == ('gcc',)",
        "assert fake.mock_calls == [call('gcc')]",
    ],
)
def test_mock_echo_negative_when_the_call_arguments_are_checked(
    verification: str,
) -> None:
    # Which arguments the mock saw is often the behaviour under test (the
    # compiler a selector picked), so checking them is not mere wiring.
    flags = _only(
        f"""
        def test_selects_compiler(monkeypatch):
            fake = FakeRunner()
            monkeypatch.setattr(module, "run", fake)
            pick_compiler("x.c")
            {verification}
        """
    )
    assert "mock_echo" not in flags


def test_mock_echo_flags_call_count_assert_under_mock_patch() -> None:
    flags = _only(
        """
        from unittest import mock

        def test_wiring():
            with mock.patch("pkg.mod.helper") as helper:
                run()
            assert helper.call_count == 1
        """
    )
    assert "mock_echo" in flags


def test_mock_echo_negative_when_a_return_value_is_asserted() -> None:
    flags = _only(
        """
        def test_behaviour(monkeypatch):
            fake = FakeHelper()
            monkeypatch.setattr(module, "helper", fake)
            result = run()
            fake.assert_called_once_with(1)
            assert result == 3
        """
    )
    assert "mock_echo" not in flags


def test_mock_echo_negative_without_any_patch() -> None:
    flags = _only(
        """
        def test_recorder():
            recorder = Recorder()
            recorder.run()
            assert recorder.called
        """
    )
    assert "mock_echo" not in flags


# ---------------------------------------------------------------------------
# set_then_read
# ---------------------------------------------------------------------------


def test_set_then_read_flags_constructor_echo() -> None:
    flags = _only(
        """
        def test_config_roundtrip():
            cfg = Config(name="alpha", size=3)
            assert cfg.name == "alpha"
        """
    )
    assert "set_then_read" in flags


@pytest.mark.parametrize(
    "body",
    [
        # Through a round-trip: the serializer could drop or mangle it.
        'cfg = Config(name="alpha")\n    back = load(dump(cfg))\n    assert back.name == "alpha"',
        # Through a merge of two inputs.
        'a = Frag(producer="clang")\n    merged = merge([a])\n    assert merged.producer == "clang"',
        # A name rebound later: which binding the assert reads is unknown.
        'cfg = Config(name="alpha")\n    cfg = normalize(cfg)\n    assert cfg.name == "alpha"',
    ],
)
def test_set_then_read_negative_when_the_value_went_through_a_transform(
    body: str,
) -> None:
    flags = _only(f"def test_transform():\n    {body}\n")
    assert "set_then_read" not in flags


def test_set_then_read_negative_when_literal_came_from_fixture_processing() -> None:
    # "2.0" is produced by normalize(), not handed in as a keyword argument.
    flags = _only(
        """
        def test_version_from_fixture():
            raw = load_fixture()
            result = normalize(raw)
            assert result.version == "2.0"
        """
    )
    assert "set_then_read" not in flags


def test_set_then_read_negative_when_another_assert_is_a_computed_value() -> None:
    flags = _only(
        """
        def test_mixed():
            cfg = Config(name="alpha")
            assert cfg.name == "alpha"
            assert cfg.count == len(items)
        """
    )
    assert "set_then_read" not in flags


def test_set_then_read_negative_when_literal_differs_from_input() -> None:
    flags = _only(
        """
        def test_transformed():
            cfg = Config(name="alpha")
            assert cfg.name == "ALPHA"
        """
    )
    assert "set_then_read" not in flags


# ---------------------------------------------------------------------------
# constant_mirror
# ---------------------------------------------------------------------------


def test_constant_mirror_flags_enum_value_pinned_to_literal() -> None:
    flags = _only(
        """
        from abicheck.model.change_catalog.kinds import ChangeKind

        def test_kind_spelling():
            assert ChangeKind.FOO.value == "foo"
        """
    )
    assert "constant_mirror" in flags


def test_constant_mirror_flags_module_constant_pinned_to_literal() -> None:
    flags = _only(
        """
        from abicheck.policy import MAX_X

        def test_limit():
            assert MAX_X == 5
        """
    )
    assert "constant_mirror" in flags


def test_constant_mirror_negative_when_compared_to_computed_value() -> None:
    flags = _only(
        """
        from abicheck.model.change_catalog.kinds import ChangeKind

        def test_kind_spelling():
            assert ChangeKind.FOO.value == expected_value()
        """
    )
    assert "constant_mirror" not in flags


def test_constant_mirror_negative_when_constant_is_not_from_abicheck() -> None:
    flags = _only(
        """
        from other_package import MAX_X

        def test_limit():
            assert MAX_X == 5
        """
    )
    assert "constant_mirror" not in flags


def test_constant_mirror_negative_when_behaviour_is_also_asserted() -> None:
    flags = _only(
        """
        from abicheck.model.change_catalog.kinds import ChangeKind

        def test_kind_spelling():
            assert ChangeKind.FOO.value == "foo"
            assert classify(sample()) is ChangeKind.FOO
        """
    )
    assert "constant_mirror" not in flags


# ---------------------------------------------------------------------------
# no_assert
# ---------------------------------------------------------------------------


def test_no_assert_flags_a_test_that_only_runs_code() -> None:
    flags = _only(
        """
        def test_smoke():
            run_thing()
        """
    )
    assert "no_assert" in flags


def test_no_assert_negative_with_expected_exception() -> None:
    flags = _only(
        """
        import pytest

        def test_rejects():
            with pytest.raises(ValueError):
                run_thing()
        """
    )
    assert "no_assert" not in flags


def test_no_assert_negative_when_delegating_to_same_file_helper() -> None:
    flags = _only(
        """
        def _check_result(value):
            assert value == 1

        def test_uses_helper():
            _check_result(run_thing())
        """
    )
    assert "no_assert" not in flags


def test_no_assert_negative_for_assert_style_call() -> None:
    flags = _only(
        """
        def test_array():
            assert_allclose(run_thing(), expected)
        """
    )
    assert "no_assert" not in flags


# ---------------------------------------------------------------------------
# trivial_size
# ---------------------------------------------------------------------------


def test_trivial_size_flags_short_type_only_test() -> None:
    flags = _only(
        """
        def test_short():
            obj = build()
            assert isinstance(obj, dict)
        """
    )
    assert "trivial_size" in flags


def test_trivial_size_ignores_leading_docstring() -> None:
    flags = _only(
        '''
        def test_documented():
            """Build returns a mapping."""
            obj = build()
            assert isinstance(obj, dict)
            assert obj is not None
        '''
    )
    assert "trivial_size" in flags


def test_trivial_size_negative_when_body_is_longer_than_three_statements() -> None:
    flags = _only(
        """
        def test_long():
            obj = build()
            other = build()
            third = build()
            assert isinstance(obj, dict)
        """
    )
    assert "type_only" in flags
    assert "trivial_size" not in flags


def test_trivial_size_negative_when_no_shape_flag_applies() -> None:
    flags = _only(
        """
        def test_value():
            assert build().version == "2.0"
        """
    )
    assert "trivial_size" not in flags


# ---------------------------------------------------------------------------
# Scanner plumbing
# ---------------------------------------------------------------------------


def test_every_flag_is_reachable_from_some_snippet() -> None:
    snippets = [
        "def test_a():\n    run()\n",
        "def test_b():\n    assert isinstance(run(), dict)\n",
        "def test_c(monkeypatch):\n    monkeypatch.setattr(m, 'f', g)\n    g.assert_called()\n",
        "def test_d():\n    c = C(a='x')\n    assert c.a == 'x'\n",
        "from abicheck.x import K\n\ndef test_e():\n    assert K.V.value == 'v'\n",
    ]
    seen: set[str] = set()
    for snippet in snippets:
        seen |= set(_only(snippet))
    assert seen == set(FLAGS)


def test_methods_are_audited_with_qualified_names() -> None:
    result = _flags(
        """
        class TestThing:
            def test_method(self):
                run()

            def helper(self):
                run()
        """
    )
    assert set(result) == {"TestThing.test_method"}
    assert "no_assert" in result["TestThing.test_method"]


def test_non_test_functions_are_not_audited() -> None:
    assert _flags("def helper():\n    assert 1 == 1\n") == {}


def test_audit_tree_and_main_write_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        textwrap.dedent(
            """
            def test_flagged():
                run()

            def test_clean():
                assert run() == 3
            """
        ),
        encoding="utf-8",
    )
    (tests_dir / "test_broken.py").write_text("def test_x(:\n", encoding="utf-8")

    flagged, total, files = audit_tree(tests_dir)
    assert total == 2
    assert files == 1
    assert [r.test for r in flagged] == ["test_flagged"]

    out = tmp_path / "audit.json"
    code = main(["--json", str(out), "--tests-dir", str(tests_dir)])
    assert code == 0
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [(r["line"], r["test"], r["flags"]) for r in rows] == [
        (2, "test_flagged", ["no_assert"])
    ]
    printed = capsys.readouterr().out
    assert "flagged (any flag): 1 of 2" in printed


# ---------------------------------------------------------------------------
# The --check ratchet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker_line",
    [
        "def test_pin():  # test-quality: ok wire-format spelling",
        "@pytest.mark.parametrize('x', [1])  # test-quality: ok smoke",
    ],
)
def test_the_exempt_marker_on_the_def_or_a_decorator_drops_the_finding(
    marker_line: str,
) -> None:
    lines = (
        [marker_line, "def test_pin(x):"]
        if marker_line.startswith("@")
        else [marker_line]
    )
    src = "import pytest\n" + "\n".join(lines) + "\n    run()\n"
    (record,) = audit_source(src)
    assert record.flags == ("no_assert",)
    assert record.exempt
    assert gated_findings([record]) == {flag: set() for flag in GATED_FLAGS}


def test_a_marker_inside_the_body_is_not_an_exemption() -> None:
    (record,) = audit_source("def test_x():\n    run()  # test-quality: ok\n")
    assert not record.exempt
    assert gated_findings([record])["no_assert"] == {"<source>::test_x"}


def _sets(**keys: list[str]) -> dict[str, set[str]]:
    return {flag: set(keys.get(flag, [])) for flag in GATED_FLAGS}


def test_evaluate_reports_new_and_fixed_keys_and_passes_an_exact_match() -> None:
    found = _sets(no_assert=["t.py::test_new", "t.py::test_old"])
    baseline = _sets(no_assert=["t.py::test_old", "t.py::test_fixed"])
    assert evaluate(found, baseline) == [
        "no_assert: NEW: t.py::test_new",
        "no_assert: fixed but still baselined (run --update): t.py::test_fixed",
    ]
    assert evaluate(found, found) == []


def test_evaluate_rejects_a_key_the_base_revision_did_not_baseline() -> None:
    found = baseline = _sets(type_only=["t.py::test_sneaked_in"])
    assert evaluate(found, baseline, base=_sets()) == [
        "type_only: baselined on this branch, absent on the base: t.py::test_sneaked_in"
    ]
    assert evaluate(found, baseline, base=baseline) == []


@pytest.mark.parametrize(
    "text",
    ['["not", "an", "object"]', '{"made_up_flag": []}', '{"no_assert": "t.py::x"}'],
)
def test_parse_baseline_rejects_malformed_documents(text: str) -> None:
    with pytest.raises(ValueError):
        parse_baseline(text)


def test_check_mode_exit_codes_end_to_end(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_s.py").write_text("def test_x():\n    run()\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    common = ["--check", "--tests-dir", str(tests_dir), "--baseline", str(baseline)]

    assert main([*common, "--update"]) == 0
    keys = json.loads(baseline.read_text(encoding="utf-8"))["no_assert"]
    assert len(keys) == 1 and keys[0].endswith("test_s.py::test_x")
    assert main(common) == 0

    # The test is fixed: its key is now stale, so the gate fails until removed.
    (tests_dir / "test_s.py").write_text(
        "def test_x():\n    assert run() == 1\n", encoding="utf-8"
    )
    assert main(common) == 1

    baseline.write_text("{not json", encoding="utf-8")
    assert main(common) == 2
