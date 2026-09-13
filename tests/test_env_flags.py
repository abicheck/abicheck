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

"""The ``ABICHECK_*`` boolean value domain, stated over the whole class.

Bug class ``config.env_flag_value_domain``. The reported instance was
``ABICHECK_CC_DISABLE=0`` disabling source-fact capture (it was read as
"any non-empty value"), but the class is every ``ABICHECK_*`` boolean knob
× every value a user might plausibly set: five hand-rolled parsers with
four different token sets had drifted apart, and a test pinned to the one
reported variable and the one reported value would have closed none of the
siblings.

So the sweep below is over :data:`~abicheck.env_flags.BOOLEAN_ENV_FLAGS`'s
whole registry × the whole value domain, driven through each variable's
**real reader** (not through the parser the readers share, which would be
the implementation asserting itself), against an oracle that is an
independent literal table rather than a second call into
:func:`~abicheck.env_flags.parse_env_flag`. Two structural guards keep it
from going quietly stale: the registry must cover every reader (no module
under ``abicheck/`` may still compare an ``ABICHECK_*`` value against a
truthy/falsey literal set of its own), and the oracle must not have
collapsed to a constant.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from abicheck import cc_wrapper
from abicheck.buildsource import build_evidence, preprocessor_facts
from abicheck.model.env_flags import BOOLEAN_ENV_FLAGS, env_flag, parse_env_flag

#: The value domain, and what each value means — written out literally,
#: independent of the parser under test. `None` means "resolves to the
#: variable's own default", which is what both "unset" and "unrecognized"
#: must produce.
VALUE_ORACLE: dict[str | None, bool | None] = {
    None: None,  # unset
    "": None,
    "   ": None,
    "1": True,
    "true": True,
    "TRUE": True,
    " True ": True,
    "yes": True,
    "on": True,
    "0": False,
    "false": False,
    "FALSE": False,
    " off ": False,
    "no": False,
    "off": False,
    "maybe": None,
    "banana": None,
    "2": None,
    "-1": None,
    "truthy": None,
}


def expected(value: str | None, default: bool) -> bool:
    """The oracle: what *value* must resolve to for a knob defaulting to
    *default*. A lookup in the literal table above, never a call into the
    parser (or into anything that calls it)."""
    meaning = VALUE_ORACLE[value]
    return default if meaning is None else meaning


def _set(monkeypatch: pytest.MonkeyPatch, name: str, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)


# ---------------------------------------------------------------------------
# Every registered knob's *real reader*, so the sweep tests shipped behaviour
# rather than the shared parser's self-consistency.
# ---------------------------------------------------------------------------


def _parallel_extraction_enabled() -> bool:
    """`resolve_sides_sequentially` is the consumer; with neither side
    carrying a dump manifest, sequential resolution happens exactly when the
    knob is off, so this inverts back to the knob's own reading."""
    from abicheck.service import CompareRequest, InputSpec
    from abicheck.service_compare_pipeline import resolve_sides_sequentially

    request = CompareRequest(
        old=InputSpec(path=Path("old.so")), new=InputSpec(path=Path("new.so"))
    )
    return not resolve_sides_sequentially(request)


def _cc_capture_enabled() -> bool:
    """The `abicheck-cc` wrapper's real public entry point: does a successful
    compile still reach fact extraction? This is the reported instance's own
    user-visible behaviour, not a predicate beside it."""
    emitted: list[object] = []

    def _runner(_command: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(_command, 0, b"", b"")

    def _emit(*_args: object, **_kwargs: object) -> None:
        emitted.append(object())
        return None

    rc = cc_wrapper.run_cc_wrapper(
        ["cc", "-c", "x.c"], runner=_runner, emit=_emit, env=None
    )
    assert rc == 0
    return bool(emitted)


#: variable -> (reader, reader_polarity_is_the_knob). Every reader returns
#: the knob's own resolved value.
READERS: dict[str, Callable[[], bool]] = {
    "ABICHECK_ALLOW_AST_FALLBACK": lambda: __import__(
        "abicheck.dumper_toolchain", fromlist=["x"]
    )._ast_fallback_enabled(),
    "ABICHECK_ALLOW_UNSUPPORTED_CASTXML": lambda: __import__(
        "abicheck.dumper_toolchain", fromlist=["x"]
    )._allow_unsupported_castxml_enabled(),
    "ABICHECK_AUTO_SYSTEM_INCLUDES": lambda: __import__(
        "abicheck.dumper_sysinc", fromlist=["x"]
    )._auto_system_includes_enabled(),
    "ABICHECK_CC_DISABLE": lambda: not _cc_capture_enabled(),
    "ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS": lambda: __import__(
        "abicheck.dumper_clang_errors", fromlist=["x"]
    )._streaming_prune_enabled(),
    "ABICHECK_COLLECT_COMDAT": build_evidence.comdat_scan_requested,
    "ABICHECK_PARALLEL_EXTRACTION": _parallel_extraction_enabled,
    "ABICHECK_PREPROCESSOR_SCAN": preprocessor_facts.preprocessor_scan_enabled,
}


def _cases() -> Iterator[tuple[str, str | None]]:
    for name in sorted(BOOLEAN_ENV_FLAGS):
        for value in VALUE_ORACLE:
            yield name, value


class TestValueDomainOverEveryKnob:
    """The class invariant: one value domain, every variable, real readers."""

    def test_every_registered_knob_has_a_reader(self) -> None:
        assert set(READERS) == set(BOOLEAN_ENV_FLAGS)

    @pytest.mark.parametrize(("name", "value"), list(_cases()))
    def test_reader_agrees_with_the_oracle(
        self, name: str, value: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch, name, value)
        want = expected(value, BOOLEAN_ENV_FLAGS[name])
        assert READERS[name]() is want, (
            f"{name}={value!r} resolved to {READERS[name]()!r}, expected {want!r}"
        )

    @pytest.mark.parametrize(("name", "value"), list(_cases()))
    def test_shared_parser_agrees_with_the_oracle(
        self, name: str, value: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch, name, value)
        assert env_flag(name) is expected(value, BOOLEAN_ENV_FLAGS[name])

    def test_reported_instance_zero_does_not_disable_capture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The originally reported input, kept as a named landmark — the
        sweep above already covers it, but a future reader searching for
        `ABICHECK_CC_DISABLE=0` should find it stated."""
        monkeypatch.setenv("ABICHECK_CC_DISABLE", "0")
        assert _cc_capture_enabled() is True
        monkeypatch.setenv("ABICHECK_CC_DISABLE", "1")
        assert _cc_capture_enabled() is False

    def test_oracle_is_not_a_constant(self) -> None:
        """Vacuity guard: an oracle accidentally reduced to one answer would
        make the whole sweep pass while asserting nothing."""
        for default in (True, False):
            answers = {expected(v, default) for v in VALUE_ORACLE}
            assert answers == {True, False}
        assert set(BOOLEAN_ENV_FLAGS.values()) == {True, False}

    def test_unregistered_name_is_rejected(self) -> None:
        with pytest.raises(KeyError):
            env_flag("ABICHECK_NOT_A_REGISTERED_KNOB")

    def test_parse_env_flag_never_flips_on_an_unreadable_value(self) -> None:
        for value in ("maybe", "", "   ", "2", None):
            assert parse_env_flag(value, default=True) is True
            assert parse_env_flag(value, default=False) is False


class TestNoHandRolledParserSurvives:
    """The structural half: the class stays closed only while every reader
    routes through the shared parser."""

    _TOKEN_RE = re.compile(r"\"(?:1|0|true|false|yes|no|on|off)\"", re.IGNORECASE)

    @staticmethod
    def _module_level_strings(tree: ast.Module) -> dict[str, str]:
        """Module-level ``NAME = "..."`` bindings, so a comparison that names
        its variable through a constant (which every reader but one does)
        still resolves to the variable it reads."""
        bound: dict[str, str] = {}
        for node in tree.body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            value = getattr(node, "value", None)
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = value.value
        return bound

    def test_no_module_compares_an_abicheck_env_value_by_hand(self) -> None:
        root = Path(__file__).resolve().parent.parent / "abicheck"
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if path.name == "env_flags.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "environ" not in source and "getenv" not in source:
                continue
            tree = ast.parse(source)
            bound = self._module_level_strings(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                segment = ast.get_source_segment(source, node) or ""
                # Keyed on the *environment read* plus the variable this
                # comparison actually names -- resolved through module-level
                # constants, because every reader but one names its variable
                # that way (`_AUTO_SYSINC_ENV`,
                # `STREAM_PRUNE_DEPENDENCY_DECLS_ENV_VAR`) and a guard
                # matching only the literal text "ABICHECK_" passes a
                # re-introduced hand-rolled parser unnoticed (verified by
                # mutation, which is the only reason this reads this way).
                if "environ" not in segment and "getenv" not in segment:
                    continue
                names = {
                    child.value
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                } | {
                    bound[child.id]
                    for child in ast.walk(node)
                    if isinstance(child, ast.Name) and child.id in bound
                }
                if not any(name.startswith("ABICHECK_") for name in names):
                    continue
                if self._TOKEN_RE.search(segment):
                    offenders.append(f"{path.name}: {segment.splitlines()[0]}")
        assert not offenders, (
            "these sites parse an ABICHECK_* boolean by hand instead of "
            f"routing through abicheck.env_flags.env_flag: {offenders}"
        )
