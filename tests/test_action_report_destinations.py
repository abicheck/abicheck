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

"""``action/run.sh``: every JSON destination the caller named, judged on its own.

Sibling of ``test_action_unreadable_report_verdict.py``, which owns the
question "is this *document* a usable result". This module owns the two
questions about *destinations* that the same defect class reaches through:

* **where** a report was asked for -- `_json_report_src` is a fallback chain
  answering "give me a report to read", and routing validation through it let a
  destination that arrived answer for a requested one that did not, while
  keying the inventory on ``$OUTPUT_FILE`` alone missed an
  ``extra-args --output`` override in both directions;
* **whether this invocation wrote it** -- parseability establishes a document
  is there, not that this run produced it, so a leftover or PR-committed report
  read as the run's own output.

Split out from that module when it crossed this repository's 1200-line test
cap. Deliberately a split by responsibility rather than a trim to fit, per the
root ``AGENTS.md``: the harness both modules need moved to
``tests/_action_run_sh_harness.py`` and is imported, not duplicated.

Bug class: ``report.unestablished_result_reads_as_success``
(``tests/regressions/manifest.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _lib,
    _run_action,
    _stub_abicheck,
    _stub_stdout_only,
)

pytestmark = REQUIRES_POSIX_SHELL


class TestEveryRepeatableWriteDestinationIsChecked:
    """`--write` is repeatable, so every `json=` destination is a real request.

    `compare` declares `--write` with `multiple=True` (ADR-068 D4, "one
    analysis, several artifacts"), and every named artifact is written. The
    extractor behind this predicate used to *clear* an already-found `json=`
    path whenever a later `--write` named another format — matching a stale
    comment that called the option scalar and last-wins. So
    `--write json=a.json --write markdown=b.md` reported no requested JSON path
    at all, and a missing `a.json` left an exit-0 run publishing COMPATIBLE
    (Codex review, P2).

    The two contradictory claims in the tree were settled against the option
    declaration itself, not either comment.
    """

    def _env(self, tmp_path: Path, extra_args: str) -> dict[str, str]:
        return {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "markdown",
            "INPUT_EXTRA_ARGS": extra_args,
        }

    def _stub_writing(self, tmp_path: Path, *, honor: set[str]) -> Path:
        """An abicheck that writes only the `json=` destinations in *honor*.

        Lets a test name two JSON destinations and have exactly one arrive —
        the shape the clearing bug hid.
        """
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        payload = json.dumps({"report_schema_version": "4.4", "verdict": "COMPATIBLE"})
        lines = ["#!/usr/bin/env bash"]
        for name in sorted(honor):
            lines.append(f"printf '%s' '{payload}' > {name}")
        lines.append("exit 0")
        stub = bindir / "abicheck"
        stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stub.chmod(0o755)
        return bindir

    def test_a_json_write_followed_by_another_format_is_still_required(
        self, tmp_path: Path
    ) -> None:
        # The exact reported case: the json destination is named first, a
        # non-json `--write` follows, and nothing writes the json one.
        target = tmp_path / "a.json"
        bindir = self._stub_writing(tmp_path, honor=set())
        outputs = _run_action(
            tmp_path,
            self._env(
                tmp_path, f"--write json={target} --write markdown={tmp_path / 'b.md'}"
            ),
            bindir,
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_the_same_combination_passes_when_the_json_destination_arrives(
        self, tmp_path: Path
    ) -> None:
        # Negative control: identical extra-args, but the stub honors the json
        # destination. Without this, the fix is satisfiable by rejecting every
        # multi-write invocation.
        target = tmp_path / "a.json"
        bindir = self._stub_writing(tmp_path, honor={str(target)})
        outputs = _run_action(
            tmp_path,
            self._env(
                tmp_path, f"--write json={target} --write markdown={tmp_path / 'b.md'}"
            ),
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_second_json_destination_is_checked_too(self, tmp_path: Path) -> None:
        # "Track all caller-supplied JSON destinations" — one arriving does not
        # excuse another that did not, so a readable first destination must not
        # mask an absent second.
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        bindir = self._stub_writing(tmp_path, honor={str(first)})
        outputs = _run_action(
            tmp_path,
            self._env(tmp_path, f"--write json={first} --write json={second}"),
            bindir,
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_both_json_destinations_arriving_passes(self, tmp_path: Path) -> None:
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        bindir = self._stub_writing(tmp_path, honor={str(first), str(second)})
        outputs = _run_action(
            tmp_path,
            self._env(tmp_path, f"--write json={first} --write json={second}"),
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_resultless_destination_is_caught(self, tmp_path: Path) -> None:
        target = tmp_path / "a.json"
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "abicheck"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s' '{{\"error\": \"write interrupted\"}}' > {target}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        outputs = _run_action(
            tmp_path, self._env(tmp_path, f"--write json={target}"), bindir
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


#: Every way one named destination can fail to hold a usable report *this run*
#: produced. `stale` is the odd one out and the point of the enumeration: the
#: document is valid, parses, carries a real verdict, and was written by some
#: earlier run — indistinguishable from a good report by content alone.
UNUSABLE_DESTINATION_STATES = (
    "absent",
    "zero bytes",
    "empty object",
    "no result",
    "truncated",
    "stale",
)

_GOOD_REPORT = json.dumps({"report_schema_version": "4.4", "verdict": "COMPATIBLE"})

_STATE_PAYLOADS = {
    "zero bytes": "",
    "empty object": "{}",
    "no result": '{"error": "write interrupted"}',
    "truncated": '{"verdict": "COMPATIBLE"',
}


class TestEveryRequestedDestinationIsJudgedOnItsOwn:
    """A requested destination must hold a report *this invocation* produced.

    Two review findings (Codex, P2, both reproduced) with one root cause: the
    validation asked "is there a readable report somewhere" rather than "did
    this run produce a usable report at every destination it was asked for".

    * `_report_validity` followed `_json_report_src`, which is a *fallback
      chain* — so a missing `format: json` primary was masked by a valid
      `extra-args --write json=secondary.json`, and the step published a
      compatibility verdict though the requested output never arrived.
    * the per-`--write` loop checked parseability without freshness — so a
      leftover document (or one a PR author committed, `extra-args` being
      PR-controlled) read as one this run had written.

    The invariant, stated over the cross-product rather than the two reported
    inputs: *for any single requested destination left in any unusable state,
    with every other destination valid and freshly written, the step must
    publish REPORT_UNREADABLE and fail.* One destination arriving never
    answers for another, and content alone never establishes authorship.
    """

    def _paths(self, tmp_path: Path) -> tuple[Path, Path]:
        return tmp_path / "primary.json", tmp_path / "secondary.json"

    def _stub(self, tmp_path: Path, writes: dict[Path, str]) -> Path:
        """An abicheck writing exactly *writes* (destination -> literal text)."""
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        lines = ["#!/usr/bin/env bash"]
        for index, (dest, text) in enumerate(sorted(writes.items())):
            # Via a file, so no payload text has to survive shell quoting.
            blob = tmp_path / f"blob{index}.bin"
            blob.write_text(text, encoding="utf-8")
            lines.append(f'cp "{blob}" "{dest}"')
        lines.append("exit 0")
        stub = bindir / "abicheck"
        stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stub.chmod(0o755)
        return bindir

    def _env(self, tmp_path: Path, primary: Path, secondary: Path) -> dict[str, str]:
        return {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "json",
            "INPUT_OUTPUT_FILE": str(primary),
            "INPUT_EXTRA_ARGS": f"--write json={secondary}",
        }

    def _arrange(
        self, tmp_path: Path, broken: str, state: str
    ) -> tuple[Path, dict[str, str]]:
        """Set up so *broken* ("primary"/"secondary") is in *state*, the other good."""
        primary, secondary = self._paths(tmp_path)
        target = primary if broken == "primary" else secondary
        other = secondary if broken == "primary" else primary
        writes = {other: _GOOD_REPORT}
        if state == "stale":
            # Pre-exists with perfectly valid content this run never wrote.
            target.write_text(_GOOD_REPORT, encoding="utf-8")
        elif state != "absent":
            writes[target] = _STATE_PAYLOADS[state]
        return self._stub(tmp_path, writes), self._env(tmp_path, primary, secondary)

    @pytest.mark.parametrize("broken", ("primary", "secondary"))
    @pytest.mark.parametrize("state", UNUSABLE_DESTINATION_STATES)
    def test_one_unusable_destination_is_never_masked_by_the_other(
        self, tmp_path: Path, broken: str, state: str
    ) -> None:
        bindir, env = self._arrange(tmp_path, broken, state)
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs.get("verdict") == "REPORT_UNREADABLE", (broken, state, outputs)
        assert outputs["_exit"] == 1, (broken, state, outputs)

    @pytest.mark.parametrize("broken", ("primary", "secondary"))
    @pytest.mark.parametrize("state", UNUSABLE_DESTINATION_STATES)
    def test_the_diagnostic_names_the_destination_that_failed(
        self, tmp_path: Path, broken: str, state: str
    ) -> None:
        # A verdict alone leaves the user hunting; and naming the *other*
        # destination is exactly the confusion the fallback chain caused.
        #
        # Scoped to the `::error::` annotations, not to stdout as a whole: every
        # destination path also appears in the echoed command line, so a
        # substring search over all output passes even when no diagnostic was
        # emitted at all (caught by mutation-testing this assertion).
        bindir, env = self._arrange(tmp_path, broken, state)
        outputs = _run_action(tmp_path, env, bindir)
        primary, secondary = self._paths(tmp_path)
        named = primary if broken == "primary" else secondary
        other = secondary if broken == "primary" else primary
        errors = [
            line
            for line in outputs["_stdout"].splitlines()
            if line.startswith("::error::")
        ]
        blamed = [line for line in errors if str(named) in line]
        assert blamed, (broken, state, errors)
        assert not [line for line in errors if str(other) in line], (
            broken,
            state,
            errors,
        )

    def test_both_destinations_freshly_written_is_compatible(
        self, tmp_path: Path
    ) -> None:
        # The control that keeps every assertion above from being satisfiable
        # by rejecting any multi-destination invocation outright.
        primary, secondary = self._paths(tmp_path)
        bindir = self._stub(tmp_path, {primary: _GOOD_REPORT, secondary: _GOOD_REPORT})
        outputs = _run_action(tmp_path, self._env(tmp_path, primary, secondary), bindir)
        assert outputs.get("verdict") == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_pre_existing_destination_the_run_overwrites_is_accepted(
        self, tmp_path: Path
    ) -> None:
        # Freshness must key on "did this run write it", not "did it pre-exist":
        # re-running a workflow step over an existing report.json is ordinary.
        primary, secondary = self._paths(tmp_path)
        primary.write_text(json.dumps({"verdict": "BREAKING"}), encoding="utf-8")
        secondary.write_text(json.dumps({"verdict": "BREAKING"}), encoding="utf-8")
        bindir = self._stub(tmp_path, {primary: _GOOD_REPORT, secondary: _GOOD_REPORT})
        outputs = _run_action(tmp_path, self._env(tmp_path, primary, secondary), bindir)
        assert outputs.get("verdict") == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_stale_destination_is_not_read_as_this_runs_verdict(
        self, tmp_path: Path
    ) -> None:
        # The forgery the freshness check exists to stop, stated as its own
        # case: a PR-committed report claiming COMPATIBLE must not be able to
        # answer for a run that wrote nothing at all.
        primary, secondary = self._paths(tmp_path)
        primary.write_text(_GOOD_REPORT, encoding="utf-8")
        secondary.write_text(_GOOD_REPORT, encoding="utf-8")
        bindir = self._stub(tmp_path, {})
        outputs = _run_action(tmp_path, self._env(tmp_path, primary, secondary), bindir)
        assert outputs.get("verdict") == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


#: Workflow-command payloads, embedded in a *destination path* rather than in
#: report content. `extra-args` is PR-controlled per `action/AGENTS.md`'s threat
#: model, so `--write json=<payload>` is a caller-supplied string that reaches a
#: `::error::` annotation whenever that destination fails to arrive.
#:
#: `%0A` is the one that matters and the one that was missed: GitHub
#: percent-decodes workflow-command *data*, so a raw `%0A` in the emitted line
#: becomes a newline on the runner and everything after it is parsed as a
#: second command. A literal newline cannot survive a shell path here; a
#: percent-encoded one can.
DESTINATION_INJECTION_PAYLOADS = (
    "x%0A::add-mask::secret",
    "x%0A::set-output name=verdict::COMPATIBLE",
    "x%0A::error::forged",
    "x%0A::stop-commands::token",
    "x%0D::notice::forged",
    "x%25%30A::error::double-encoded",
)


class TestADestinationPathCannotForgeAWorkflowCommand:
    """The path in the diagnostic is attacker-controlled, so attack it.

    Written as an executed attack rather than an assertion over `run.sh`'s
    text, for the reason this repository already paid for once (#705 -> #758):
    a defense asserted by reading the defending file passed while the next
    payload went straight through it.

    The invariant: for any caller-supplied destination path, no line this step
    emits may contain a decodable workflow-command separator that the path put
    there. `_sanitize_annotation` escapes `%` first and then flattens CR/LF, so
    a `%0A` in the input reaches the log as the literal text `%250A` --
    displayed, never decoded.
    """

    @pytest.mark.parametrize("payload", DESTINATION_INJECTION_PAYLOADS)
    def test_no_raw_percent_escape_from_the_path_reaches_the_log(
        self, tmp_path: Path, payload: str
    ) -> None:
        dest = tmp_path / payload
        bindir = self._stub_that_writes_nothing(tmp_path)
        outputs = _run_action(tmp_path, self._env(tmp_path, dest), bindir)
        # The step must still fail -- the attack must not be prevented by the
        # destination quietly being treated as satisfied.
        assert outputs.get("verdict") == "REPORT_UNREADABLE", outputs
        errors = [
            line
            for line in outputs["_stdout"].splitlines()
            if line.startswith("::error::")
        ]
        assert errors, outputs["_stdout"]
        for line in errors:
            assert "%0A" not in line.upper().replace("%0D", "%0A") or "%25" in line, (
                payload,
                line,
            )
            # The decisive check: the payload's own escape must appear escaped.
            assert payload not in line, (payload, line)
            assert "%250A" in line or "%250D" in line or "%25" in line, (payload, line)

    @pytest.mark.parametrize("payload", DESTINATION_INJECTION_PAYLOADS)
    def test_the_forged_command_never_becomes_its_own_line(
        self, tmp_path: Path, payload: str
    ) -> None:
        # Even if a future change decoded before emitting, the smuggled command
        # must not end up as a line of its own that a runner would execute.
        dest = tmp_path / payload
        bindir = self._stub_that_writes_nothing(tmp_path)
        outputs = _run_action(tmp_path, self._env(tmp_path, dest), bindir)
        smuggled = payload.split("%0A")[-1].split("%0D")[-1]
        for line in outputs["_stdout"].splitlines():
            assert line.strip() != smuggled.strip(), (payload, line)

    def test_an_ordinary_path_is_still_named_in_full(self, tmp_path: Path) -> None:
        # Narrowness control: sanitizing must not mangle the common case, or
        # the diagnostic stops telling the user which artifact went missing.
        dest = tmp_path / "reports" / "abi.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        bindir = self._stub_that_writes_nothing(tmp_path)
        outputs = _run_action(tmp_path, self._env(tmp_path, dest), bindir)
        errors = [
            line
            for line in outputs["_stdout"].splitlines()
            if line.startswith("::error::")
        ]
        assert any(str(dest) in line for line in errors), errors

    def _stub_that_writes_nothing(self, tmp_path: Path) -> Path:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "abicheck"
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        return bindir

    def _env(self, tmp_path: Path, dest: Path) -> dict[str, str]:
        return {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "markdown",
            "INPUT_EXTRA_ARGS": f"--write json={dest}",
        }


#: Strings that parse, are non-empty, and are not verdicts any consumer can act
#: on. The reported instance was `"write interrupted"`; the class is "anything
#: outside the emitters' own vocabulary".
UNKNOWN_VERDICT_STRINGS = (
    "write interrupted",
    "compatible",
    "OK",
    "PASS",
    "BREAKING_CHANGE",
    "COMPATIBLE ",
    "null",
    "0",
)


class TestAnUnknownVerdictStringIsNotAResult:
    """`{"verdict": "<anything>"}` must not license the COMPATIBLE fallthrough.

    `_carries_a_result` accepted any non-empty verdict string, so a document
    like `{"verdict": "write interrupted"}` read as `ok`; `compat_verdict` then
    returned that unknown value, and `_resolve_clean_exit_verdict` -- which
    recognizes only the break and risk tiers -- kept its initial COMPATIBLE
    (Codex review, P2). Membership in the emitters' own vocabulary is what the
    reader now requires.
    """

    @pytest.mark.parametrize("verdict", UNKNOWN_VERDICT_STRINGS)
    def test_it_never_publishes_compatible(self, tmp_path: Path, verdict: str) -> None:
        payload = json.dumps({"report_schema_version": "4.4", "verdict": verdict})
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode("utf-8"))
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") == "REPORT_UNREADABLE", (verdict, outputs)
        assert outputs["_exit"] == 1, (verdict, outputs)

    @pytest.mark.parametrize(
        "verdict",
        ("NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK", "BREAKING"),
    )
    def test_every_real_verdict_still_reads(self, tmp_path: Path, verdict: str) -> None:
        # The control that keeps the vocabulary from being satisfiable by
        # rejecting everything: each tier the CLI actually emits must pass.
        payload = json.dumps({"report_schema_version": "4.4", "verdict": verdict})
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode("utf-8"))
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") != "REPORT_UNREADABLE", (verdict, outputs)


class TestAnExtraArgsOutputOverrideIsHonoured:
    """`extra-args --output PATH` is where the report really lands.

    `run.sh` puts its own `-o "$OUTPUT_FILE"` on `CMD` first and
    `$INPUT_EXTRA_ARGS` last, and Click keeps the last occurrence of a repeated
    option -- the same override `_effective_format` already existed to resolve
    for `--format`. Keying the destination inventory on `$OUTPUT_FILE` alone got
    it wrong in both directions (Codex review, P2, reproduced):

    * with no `output-file` input, `format: json` plus `extra-args: --output
      report.json` named no destination at all, so the run was validated as the
      *stdout* shape -- where the capture is empty, because output went to a
      file -- and a working run published REPORT_UNREADABLE;
    * with both given, the inventory validated the superseded path while the
      real report landed elsewhere.

    The reading chain is fixed alongside the inventory on purpose. Had only the
    inventory learned the effective path, the report would validate and then
    still not be *read*, which is the original defect of this PR: a readable
    result nothing consults, falling through to COMPATIBLE.
    """

    def _stub(self, tmp_path: Path, dest: Path, verdict: str = "BREAKING") -> Path:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        blob = tmp_path / "payload.json"
        blob.write_text(
            json.dumps({"report_schema_version": "4.4", "verdict": verdict}),
            encoding="utf-8",
        )
        stub = bindir / "abicheck"
        stub.write_text(
            f'#!/usr/bin/env bash\ncp "{blob}" "{dest}"\nexit 0\n', encoding="utf-8"
        )
        stub.chmod(0o755)
        return bindir

    def _env(self, tmp_path: Path, extra: str, output_file: Path | None) -> dict:
        env = {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "json",
            "INPUT_EXTRA_ARGS": extra,
        }
        if output_file is not None:
            env["INPUT_OUTPUT_FILE"] = str(output_file)
        return env

    @pytest.mark.parametrize("spelling", ("--output", "-o", "--output="))
    def test_an_override_with_no_output_file_input_is_not_unreadable(
        self, tmp_path: Path, spelling: str
    ) -> None:
        dest = tmp_path / "override.json"
        joiner = "" if spelling.endswith("=") else " "
        bindir = self._stub(tmp_path, dest)
        outputs = _run_action(
            tmp_path, self._env(tmp_path, f"{spelling}{joiner}{dest}", None), bindir
        )
        assert outputs.get("verdict") != "REPORT_UNREADABLE", (spelling, outputs)

    @pytest.mark.parametrize("spelling", ("--output", "-o", "--output="))
    def test_the_report_at_the_override_is_actually_read(
        self, tmp_path: Path, spelling: str
    ) -> None:
        # The half that a fix to the inventory alone would miss: the verdict
        # must come from the overridden path, not fall through to COMPATIBLE.
        dest = tmp_path / "override.json"
        joiner = "" if spelling.endswith("=") else " "
        bindir = self._stub(tmp_path, dest, verdict="BREAKING")
        outputs = _run_action(
            tmp_path, self._env(tmp_path, f"{spelling}{joiner}{dest}", None), bindir
        )
        assert outputs.get("verdict") == "BREAKING", (spelling, outputs)

    def test_an_override_supersedes_the_output_file_input(self, tmp_path: Path) -> None:
        # Both given: the real report lands at the override, and the superseded
        # input path is never written. Validating the latter would fail a
        # working run; reading it would publish a verdict from nothing.
        superseded = tmp_path / "input_path.json"
        dest = tmp_path / "override.json"
        bindir = self._stub(tmp_path, dest, verdict="API_BREAK")
        outputs = _run_action(
            tmp_path, self._env(tmp_path, f"--output {dest}", superseded), bindir
        )
        assert outputs.get("verdict") == "API_BREAK", outputs
        assert not superseded.exists(), "the stub should not have written it"

    def test_a_genuinely_missing_override_is_still_caught(self, tmp_path: Path) -> None:
        # Narrowness control: resolving the effective path must not turn into
        # "never require the primary". A stub that writes nothing still fails.
        dest = tmp_path / "override.json"
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "abicheck"
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        outputs = _run_action(
            tmp_path, self._env(tmp_path, f"--output {dest}", None), bindir
        )
        assert outputs.get("verdict") == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_real_stdout_mode_is_untouched(self, tmp_path: Path) -> None:
        # With neither an input nor an override there is genuinely no file
        # destination, and the stdout shape must keep working.
        bindir = _stub_stdout_only(
            tmp_path,
            stdout=json.dumps({"report_schema_version": "4.4", "verdict": "BREAKING"}),
        )
        env = self._env(tmp_path, "", None)
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs.get("verdict") == "BREAKING", outputs


class TestStdoutModeIsJudgedEvenBesideAWriteDestination:
    """A valid `--write json=` secondary must not excuse an unusable stdout report.

    `format: json` with no `output-file` is the documented stdout mode. The
    stdout validation was gated on *the destination inventory being empty*, but
    adding `extra-args --write json=b.json` makes it non-empty while stdout is
    still where the requested report goes -- so the check was skipped and a valid
    secondary masked an unusable stdout document. That is the same masking this
    whole area exists to close, reintroduced one branch over (CodeRabbit review,
    Major; Codex flagged the sibling shape).

    The condition is now the *mode* -- json format, no effective output path --
    and the stdout report is judged by its own captured file rather than through
    `_json_report_src`, whose fallback would reach the secondary in exactly the
    case a missing stdout report presents.
    """

    def _env(self, tmp_path: Path, secondary: Path | None) -> dict[str, str]:
        env = {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "json",
        }
        if secondary is not None:
            env["INPUT_EXTRA_ARGS"] = f"--write json={secondary}"
        return env

    def _stub(self, tmp_path: Path, *, stdout: str, secondary: Path | None) -> Path:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        lines = ["#!/usr/bin/env bash"]
        if secondary is not None:
            blob = tmp_path / "secondary.json"
            blob.write_text(
                json.dumps({"report_schema_version": "4.4", "verdict": "COMPATIBLE"}),
                encoding="utf-8",
            )
            lines.append(f'cp "{blob}" "{secondary}"')
        if stdout:
            out = tmp_path / "stdout.txt"
            out.write_text(stdout, encoding="utf-8")
            lines.append(f'cat "{out}"')
        lines.append("exit 0")
        stub = bindir / "abicheck"
        stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stub.chmod(0o755)
        return bindir

    @pytest.mark.parametrize(
        "stdout,label",
        (
            ("", "printed nothing"),
            ("Killed\n", "not json"),
            ("{}", "empty object"),
            ('{"error": "write interrupted"}', "no result"),
            ('{"verdict": "COMPATIBLE"', "truncated"),
        ),
    )
    def test_a_valid_secondary_does_not_excuse_stdout(
        self, tmp_path: Path, stdout: str, label: str
    ) -> None:
        secondary = tmp_path / "b.json"
        bindir = self._stub(tmp_path, stdout=stdout, secondary=secondary)
        outputs = _run_action(tmp_path, self._env(tmp_path, secondary), bindir)
        assert outputs.get("verdict") == "REPORT_UNREADABLE", (label, outputs)
        assert outputs["_exit"] == 1, (label, outputs)

    def test_a_good_stdout_report_beside_a_secondary_still_passes(
        self, tmp_path: Path
    ) -> None:
        # Control: the combination itself must remain usable, or the fix is
        # satisfiable by rejecting stdout mode whenever a --write is present.
        secondary = tmp_path / "b.json"
        bindir = self._stub(
            tmp_path,
            stdout=json.dumps({"report_schema_version": "4.4", "verdict": "BREAKING"}),
            secondary=secondary,
        )
        outputs = _run_action(tmp_path, self._env(tmp_path, secondary), bindir)
        assert outputs.get("verdict") == "BREAKING", outputs

    def test_plain_stdout_mode_is_unchanged(self, tmp_path: Path) -> None:
        # No secondary at all: the pre-existing stdout path must behave as before.
        bindir = self._stub(
            tmp_path,
            stdout=json.dumps({"report_schema_version": "4.4", "verdict": "BREAKING"}),
            secondary=None,
        )
        outputs = _run_action(tmp_path, self._env(tmp_path, None), bindir)
        assert outputs.get("verdict") == "BREAKING", outputs
