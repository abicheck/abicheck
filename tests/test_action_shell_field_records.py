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

"""A value crossing from Python to a shell keeps exactly its own bytes.

**Bug class:** ``boundary.platform_dependent_record_separator`` -- two
languages agreeing on a record separator that one of them silently
rewrites per platform.

**Reported by CI.** ``unit-tests (windows-latest, 3.13)`` failed
``test_a_clean_report_under_on_changes_publishes_nothing`` with
``assert 'dry-run' == 'no-changes'``. Python's text-mode ``print`` emits
``\\r\\n`` on Windows; bash's ``read -r`` strips only the ``\\n``. So
``PLAN_ACTION`` was ``"skip\\r"``, ``[[ "$PLAN_ACTION" == "skip" ]]`` was
false for a plan that said exactly ``skip``, and the publisher fell through
to a later branch.

**Why the class and not the instance.** That one assertion was the visible
end of five separate inline emitters across the two Actions, and the
publisher's skip branch was the *least* costly of them. The same cut put a
trailing carriage return on ``artifact_id`` and ``pr_number`` -- both
interpolated straight into a GitHub API path -- and on ``comment_id``, which
selects the comment a PATCH rewrites. None of those had a test that would
have failed, on any platform, because every one of them was a separate copy
of the same two-line contract.

The fix gives that contract one owner (``emit-fields``), so the invariants
below are stated over *it*, for every value shape the two shells read,
rather than over any one call site.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import click
import pytest

from abicheck.frontends.action.cli import action_cli

#: Every field the two Actions' shells actually read back, with a value of
#: the shape that field really carries. Read off `actions/*/run.sh` -- this
#: is the whole population the bug affected.
SHELL_READ_FIELDS: dict[str, object] = {
    "action": "skip",
    "comment_id": 918273645,
    "skipped_reason": "no-changes",
    "body_bytes": 4096,
    "html_url": "https://github.com/o/r/pull/1#issuecomment-918273645",
    "head_sha": "cb6102d85eaec62874f311646223cfbc1ef3cb25",
    "code": "ambiguous-pull-request",
    "pr_number": 1311,
    "pr_head_sha": "9bc92e635fbfae3252189d3bd09059cc28599490",
    "tested_sha": "3737f9f960ae14a7b24dbb6e9b686e49fb563673",
    "from_fork": True,
    "artifact_id": 10431156955,
}


def emit(document: Path, *fields: str, tolerant: bool = False) -> bytes:
    """Run the real command and return its stdout as *bytes*.

    Bytes, not text: `text=True` would apply universal-newline decoding and
    turn a `\\r\\n` back into `\\n`, which is precisely the translation under
    test. A test that could not observe the regression would pass against
    the bug.
    """
    argv = [
        sys.executable,
        "-m",
        "abicheck.frontends.action.cli",
        "emit-fields",
    ]
    if tolerant:
        argv.append("--tolerant")
    argv.extend([str(document), *fields])
    result = subprocess.run(argv, capture_output=True, check=True)
    return result.stdout


def write(tmp_path: Path, payload: object, name: str = "doc.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestTheSeparatorIsNeverPlatformDependent:
    """The invariant the Windows lane caught, stated for every field."""

    @pytest.mark.parametrize("field", sorted(SHELL_READ_FIELDS))
    def test_each_record_ends_in_one_bare_newline(
        self, tmp_path: Path, field: str
    ) -> None:
        document = write(tmp_path, {field: SHELL_READ_FIELDS[field]})
        out = emit(document, field)
        assert b"\r" not in out, (
            f"{field}: a carriage return reaches the shell, which `read -r` "
            "does not strip -- this is the Windows regression"
        )
        assert out.endswith(b"\n")
        assert out.count(b"\n") == 1

    def test_the_whole_shell_read_population_at_once(self, tmp_path: Path) -> None:
        """All twelve in one call: a per-field test cannot catch a separator
        that only goes wrong between records."""
        fields = sorted(SHELL_READ_FIELDS)
        out = emit(write(tmp_path, SHELL_READ_FIELDS), *fields)
        assert b"\r" not in out
        assert out.decode("utf-8").split("\n")[:-1] == [
            "true" if SHELL_READ_FIELDS[f] is True else str(SHELL_READ_FIELDS[f])
            for f in fields
        ]


class TestTheWindowsTranslationCannotReachTheRecords:
    """The one class of test that catches this defect on a Linux runner.

    Every assertion above runs green on Linux against the *broken* code,
    because Linux's ``os.linesep`` is already ``"\n"`` -- so they only ever
    had teeth on the one lane that reported the bug. These call the command
    in-process with ``sys.stdout`` replaced by a text layer that translates
    exactly the way Windows' does, which is what makes the regression
    reproducible everywhere.

    It is also what caught the first version of this fix: it wrote the
    records with ``sys.stdout.write``, which goes through that same
    translating layer and so carried the identical bug.
    """

    @staticmethod
    def _emit_through_a_translating_stdout(
        monkeypatch: pytest.MonkeyPatch, document: Path, *fields: str
    ) -> bytes:
        """Return the bytes that reach the underlying binary stream."""
        raw = io.BytesIO()
        # `newline="\r\n"` is what a Windows `sys.stdout` does to every
        # "\n" written through it. `closefd`-less BytesIO keeps the bytes
        # readable after the wrapper is done with them.
        wrapper = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
        monkeypatch.setattr(sys, "stdout", wrapper)
        try:
            action_cli.main(
                ["emit-fields", str(document), *fields],
                standalone_mode=False,
            )
        finally:
            wrapper.flush()
        return raw.getvalue()

    def test_no_carriage_return_survives_a_windows_shaped_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = write(tmp_path, {"action": "skip", "comment_id": 7})
        out = self._emit_through_a_translating_stdout(
            monkeypatch, document, "action", "comment_id"
        )
        assert out == b"skip\n7\n", (
            "the records were translated on the way out; a shell's `read -r` "
            "strips only the \\n, so every value would carry a stray \\r"
        )

    def test_the_exact_reported_comparison_holds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`[[ "$PLAN_ACTION" == "skip" ]]` -- the shell test that was false
        for a plan whose action was exactly `skip`."""
        document = write(tmp_path, {"action": "skip"})
        record = self._emit_through_a_translating_stdout(
            monkeypatch, document, "action"
        )
        plan_action = record.decode("utf-8").split("\n")[0]
        assert plan_action == "skip"


class TestTheFramingIsPositionalAndStaysAligned:
    """Record *count* is the contract, because the shell reads by position."""

    def test_an_absent_field_still_occupies_its_own_record(
        self, tmp_path: Path
    ) -> None:
        """The failure a dropped record causes is silent and total: every
        later field lands on the wrong variable."""
        document = write(tmp_path, {"action": "create", "body_bytes": 12})
        assert emit(document, "action", "comment_id", "body_bytes") == (
            b"create\n\n12\n"
        )

    @pytest.mark.parametrize(
        "value",
        ["", None, 0, False],
        ids=["empty-string", "null", "zero", "false"],
    )
    def test_a_falsey_value_is_one_record_not_zero(
        self, tmp_path: Path, value: object
    ) -> None:
        out = emit(write(tmp_path, {"a": 1, "b": value, "c": 3}), "a", "b", "c")
        assert out.count(b"\n") == 3
        assert out.split(b"\n")[0] == b"1"
        assert out.split(b"\n")[2] == b"3"

    def test_requesting_n_fields_always_emits_n_records(self, tmp_path: Path) -> None:
        """Over a document sharing no key at all with the request."""
        document = write(tmp_path, {"unrelated": "value"})
        for count in range(1, 8):
            fields = [f"absent_{i}" for i in range(count)]
            assert emit(document, *fields).count(b"\n") == count


class TestAValueCannotForgeARecord:
    """A line break in a value would shift every field after it."""

    @pytest.mark.parametrize(
        "hostile",
        ["a\nb", "a\rb", "a\r\nb", "\n", "\r", "trailing\n", "skip\nstale"],
        ids=[
            "lf",
            "cr",
            "crlf",
            "lone-lf",
            "lone-cr",
            "trailing-lf",
            "forges-a-plan-action",
        ],
    )
    def test_it_is_refused_rather_than_emitted(
        self, tmp_path: Path, hostile: str
    ) -> None:
        document = write(tmp_path, {"code": hostile})
        with pytest.raises(subprocess.CalledProcessError) as caught:
            emit(document, "code")
        assert b"line break" in caught.value.stderr

    def test_a_hostile_value_cannot_be_laundered_through_tolerant_mode(
        self, tmp_path: Path
    ) -> None:
        """`--tolerant` forgives an unreadable *document*, never a value that
        would break the framing of a readable one."""
        document = write(tmp_path, {"html_url": "https://e.test/\nid=2"})
        with pytest.raises(subprocess.CalledProcessError):
            emit(document, "html_url", tolerant=True)


class TestTolerance:
    """Two of the five call sites must survive a missing document."""

    def test_an_unreadable_document_is_empty_records_under_tolerant(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "never-written.json"
        assert emit(missing, "code", tolerant=True) == b"\n"
        assert emit(missing, "a", "b", tolerant=True) == b"\n\n"

    @pytest.mark.parametrize(
        "content",
        ["", "not json at all", "[1, 2, 3]", "null", '"a string"'],
        ids=["empty", "garbage", "array", "null", "scalar"],
    )
    def test_a_non_object_document_yields_empty_records_under_tolerant(
        self, tmp_path: Path, content: str
    ) -> None:
        document = tmp_path / "odd.json"
        document.write_text(content, encoding="utf-8")
        assert emit(document, "code", tolerant=True) == b"\n"

    def test_without_tolerant_an_unreadable_document_fails_loudly(
        self, tmp_path: Path
    ) -> None:
        """The default, because a plan document this package just wrote and
        cannot now read is a real failure, not an empty answer."""
        with pytest.raises(subprocess.CalledProcessError):
            emit(tmp_path / "never-written.json", "action")


def test_no_shell_reads_a_value_through_its_own_inline_python() -> None:
    """The class stays closed: a new inline emitter re-opens it.

    This is the executable half of "give the contract one owner". Without
    it, the next value a shell needs gets another `python - <<PYEOF` that
    restates the separator, and nothing anywhere fails.
    """
    actions = Path(__file__).resolve().parents[1] / "actions"
    offenders = []
    for script in sorted(actions.rglob("run.sh")):
        text = script.read_text(encoding="utf-8")
        for marker in ("<<'PYEOF'", "python -c"):
            if marker in text:
                offenders.append(f"{script.relative_to(actions.parent)}: {marker}")
    assert not offenders, (
        "an Action shell reads a value through inline Python again; route it "
        "through `emit-fields`, which owns the record separator: "
        + ", ".join(offenders)
    )


class TestFlattenPages:
    """``gh api --paginate`` emits one array per page, back to back.

    That byte stream is not a JSON document, so it needs a real decoder walk
    rather than a parse. It moved out of the shell alongside ``emit-fields``
    -- not because it had the newline bug (it writes a file, not a record),
    but because the guard above is only honest if it is total, and inline
    logic in an Action shell is what ADR-073 put in Python to begin with.
    """

    @staticmethod
    def flatten(tmp_path: Path, raw: str) -> object:
        source = tmp_path / "pulls.raw"
        source.write_text(raw, encoding="utf-8")
        out = tmp_path / "pulls.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck.frontends.action.cli",
                "flatten-pages",
                str(source),
                str(out),
            ],
            capture_output=True,
            check=True,
        )
        return json.loads(out.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("pages", [0, 1, 2, 5])
    def test_concatenated_pages_flatten_to_one_array(
        self, tmp_path: Path, pages: int
    ) -> None:
        """The count is the point: a walk that stopped after the first page
        would silently drop every pull request after it, which is how an
        association check starts passing for the wrong PR."""
        expected = [{"number": i} for i in range(pages)]
        raw = "".join(json.dumps([item]) for item in expected)
        assert self.flatten(tmp_path, raw) == expected

    @pytest.mark.parametrize(
        "separator",
        ["", "\n", "\r\n", "  ", "\n\n\t"],
        ids=["none", "lf", "crlf", "spaces", "mixed"],
    )
    def test_whitespace_between_pages_is_irrelevant(
        self, tmp_path: Path, separator: str
    ) -> None:
        raw = separator.join(['[{"number": 1}]', '[{"number": 2}]'])
        assert self.flatten(tmp_path, raw) == [{"number": 1}, {"number": 2}]

    def test_an_empty_page_contributes_nothing_but_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        raw = '[][{"number": 3}][]'
        assert self.flatten(tmp_path, raw) == [{"number": 3}]

    def test_a_non_array_page_is_kept_not_dropped(self, tmp_path: Path) -> None:
        """Dropping it would look to the caller like an empty page, which
        reads as "no associated pull request" -- a refusal for the wrong
        reason. The caller decides what an unexpected shape means."""
        assert self.flatten(tmp_path, '{"message": "Not Found"}') == [
            {"message": "Not Found"}
        ]

    def test_an_empty_document_is_an_empty_array(self, tmp_path: Path) -> None:
        assert self.flatten(tmp_path, "") == []
        assert self.flatten(tmp_path, "   \n  ") == []


class TestInProcessBranchesASubprocessCannotReach:
    """The same commands driven in-process, through the real Click entry.

    Not a convenience duplicate of the subprocess tests above: those prove
    what a real process writes to a real pipe, which is the claim that
    matters to bash, but a real process *always* has a `sys.stdout.buffer`,
    so the no-binary-layer fallback is unreachable from one. These reach it,
    and the refusal/tolerant branches, by driving `action_cli.main` directly.
    """

    @staticmethod
    def _run(argv: list[str], stdout: object | None = None) -> None:
        previous = sys.stdout
        if stdout is not None:
            sys.stdout = stdout  # type: ignore[assignment]
        try:
            action_cli.main(argv, standalone_mode=False)
        finally:
            sys.stdout = previous

    def test_the_fallback_writes_when_stdout_has_no_binary_layer(
        self, tmp_path: Path
    ) -> None:
        """A capturing stand-in with no `.buffer`: the records must still
        come out untranslated, via the reconfigure path."""

        class NoBufferStdout(io.StringIO):
            """`io.StringIO` has no `.buffer`; it does have `reconfigure`."""

            reconfigured_newline: object = "unset"

            def reconfigure(self, **kwargs: object) -> None:
                type(self).reconfigured_newline = kwargs.get("newline")

        stdout = NoBufferStdout()
        self._run(["emit-fields", str(write(tmp_path, {"a": "x"})), "a"], stdout)
        assert stdout.getvalue() == "x\n"
        assert NoBufferStdout.reconfigured_newline == "", (
            "the fallback must turn translation off rather than write through it"
        )

    def test_the_fallback_still_writes_when_reconfigure_is_absent(
        self, tmp_path: Path
    ) -> None:
        """Neither a binary layer nor `reconfigure` — the records still go
        out rather than the command raising `AttributeError`."""

        class Minimal:
            def __init__(self) -> None:
                self.written: list[str] = []

            def write(self, text: str) -> int:
                self.written.append(text)
                return len(text)

            def flush(self) -> None:
                return None

        stdout = Minimal()
        self._run(["emit-fields", str(write(tmp_path, {"a": 1})), "a"], stdout)
        assert "".join(stdout.written) == "1\n"

    def test_a_line_break_refusal_names_the_offending_field(
        self, tmp_path: Path
    ) -> None:
        document = write(tmp_path, {"safe": "ok", "hostile": "a\nb"})
        with pytest.raises(click.ClickException) as caught:
            self._run(["emit-fields", str(document), "safe", "hostile"])
        assert "'hostile'" in str(caught.value)
        assert "line break" in str(caught.value)

    def test_an_unreadable_document_propagates_without_tolerant(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(OSError):
            self._run(["emit-fields", str(tmp_path / "absent.json"), "a"])

    def test_malformed_json_propagates_without_tolerant(self, tmp_path: Path) -> None:
        document = tmp_path / "bad.json"
        document.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            self._run(["emit-fields", str(document), "a"])

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('[{"n": 1}][{"n": 2}]', [{"n": 1}, {"n": 2}]),
            ("[]", []),
            ("", []),
            ('{"message": "x"}', [{"message": "x"}]),
            ("[1,2]\n[3]\n", [1, 2, 3]),
        ],
        ids=["two-pages", "empty-page", "empty-doc", "non-array", "newline-split"],
    )
    def test_flatten_pages_in_process(
        self, tmp_path: Path, raw: str, expected: list[object]
    ) -> None:
        source = tmp_path / "raw.json"
        source.write_text(raw, encoding="utf-8")
        out = tmp_path / "out.json"
        self._run(["flatten-pages", str(source), str(out)])
        assert json.loads(out.read_text(encoding="utf-8")) == expected
