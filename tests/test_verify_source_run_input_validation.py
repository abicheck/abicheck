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

"""``verify-source-run``'s own input validation, executed as real shell.

**Bug class:** ``guard.a_misspelling_silently_takes_the_permissive_branch``
-- a flag compared with ``!= "true"``, where every value that is not exactly
that word lands on the *permissive* side. ``require-provenance: ture`` then
disables the requirement it was written to impose, and nothing anywhere
says so. The same shape covers ``provenance-from``/``report-from``: both are
concatenated onto the extraction destination, so a ``..`` component names a
file the bounded extractor never staged and never checked.

The third rule here is a *reporting* one rather than a refusal:
``tested-sha-source`` must distinguish a caller-stated commit from the run's
own head. Collapsing them tells a consumer the Action fell back when it did
not -- an inaccurate provenance label on the one output whose whole purpose
is provenance.

These are executed, not asserted as text. Each guard is extracted from the
real ``run.sh`` by name and run under a real bash, because the first version
of ``_require_member_name`` refused **every valid name** -- its loop ended in
a trailing ``&&``, making the loop's status that of the final false test --
and a text assertion would have called that shipped code correct.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _workflow_exec import bash_executable, require_bash  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="needs a real POSIX shell"
)

RUN_SH = (
    Path(__file__).resolve().parents[1] / "actions" / "verify-source-run" / "run.sh"
)
SOURCE = RUN_SH.read_text(encoding="utf-8")


def _function(name: str) -> str:
    """The real body of one shell function, by name, from the real file."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(?:.*?\n)*?^\}}$", SOURCE, re.M)
    assert match is not None, f"{name} is no longer defined in {RUN_SH.name}"
    return match.group(0)


class TestTheExtractionItself:
    """Vacuity guard: a rename would otherwise empty every case below."""

    def test_the_guard_function_is_found(self) -> None:
        body = _function("_require_member_name")
        assert "_fail" in body
        assert body.count("\n") > 3

    def test_a_name_that_does_not_exist_is_an_error_not_an_empty_string(self) -> None:
        with pytest.raises(AssertionError):
            _function("_no_such_function_here")


def _check_member(value: str) -> subprocess.CompletedProcess[str]:
    require_bash()
    script = (
        '_fail() { echo "REFUSED: $*" >&2; exit 3; }\n'
        + _function("_require_member_name")
        + '\n_require_member_name "report-from" "$1"\necho ACCEPTED\n'
    )
    return subprocess.run(
        [bash_executable(), "-c", script, "bash", value],
        capture_output=True,
        text=True,
        check=False,
    )


class TestAMemberNameStaysInsideTheArtifact:
    """Refuse an escape; and, just as much, accept an ordinary name."""

    ACCEPTED = (
        "report.json",
        "reports/report.json",
        "a/b/c/report.json",
        "with space/report.json",
        "dot.in.name.json",
        "..leading-dots-in-a-name.json",
        "trailing..json",
        "",
    )

    REFUSED = (
        "../report.json",
        "a/../../report.json",
        "a/b/..",
        "..",
        ".",
        "./report.json",
        "a/./b.json",
        "/etc/passwd",
        "/",
        "a\\b.json",
        "..\\..\\x",
    )

    @pytest.mark.parametrize("value", ACCEPTED)
    def test_an_ordinary_member_name_is_accepted(self, value: str) -> None:
        # The half that caught the real defect. A guard that refuses
        # everything satisfies every "escape is refused" assertion while
        # breaking the feature outright, so this half is not optional.
        result = _check_member(value)
        assert result.returncode == 0, result.stderr
        assert "ACCEPTED" in result.stdout

    @pytest.mark.parametrize("value", REFUSED)
    def test_an_escaping_member_name_is_refused(self, value: str) -> None:
        result = _check_member(value)
        assert result.returncode != 0, result.stdout
        assert "REFUSED" in result.stderr
        assert "ACCEPTED" not in result.stdout


class TestRequireProvenanceIsExactlyTrueOrFalse:
    """Every other spelling refuses, rather than taking the lax branch."""

    #: The whole point: each of these once meant "do not require".
    #: `""` is deliberately absent: an unset or empty Action input takes the
    #: declared `true` default via `${INPUT_...:-true}`, which is the
    #: documented behaviour and the *strict* side, not a near miss.
    NEAR_MISSES = ("ture", "True", "TRUE", "1", "yes", " true", "true ", "no", "0")

    def _run(self, value: str) -> subprocess.CompletedProcess[str]:
        require_bash()
        match = re.search(
            r'REQUIRE_PROVENANCE="\$\{INPUT_REQUIRE_PROVENANCE:-true\}"\n'
            r"case \"\$REQUIRE_PROVENANCE\" in\n(?:.*?\n)*?esac",
            SOURCE,
        )
        assert match is not None, "the require-provenance validation moved"
        script = (
            '_fail() { echo "REFUSED: $*" >&2; exit 3; }\n'
            + match.group(0)
            + '\necho "OK:$REQUIRE_PROVENANCE"\n'
        )
        return subprocess.run(
            [bash_executable(), "-c", script],
            capture_output=True,
            text=True,
            check=False,
            env={"INPUT_REQUIRE_PROVENANCE": value, "PATH": "/usr/bin:/bin"},
        )

    @pytest.mark.parametrize("value", ["true", "false"])
    def test_the_two_legal_values_are_accepted(self, value: str) -> None:
        result = self._run(value)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"OK:{value}"

    def test_an_unset_input_takes_the_strict_default(self) -> None:
        # The one value that is not a near miss, and it must land on the
        # requiring side -- a validator that refused it would break every
        # caller who never named the flag.
        result = self._run("")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK:true"

    @pytest.mark.parametrize("value", NEAR_MISSES)
    def test_a_near_miss_refuses_rather_than_defaulting_to_permissive(
        self, value: str
    ) -> None:
        result = self._run(value)
        assert result.returncode != 0, result.stdout
        assert "REFUSED" in result.stderr


class TestTestedShaSourceNamesWhereTheValueCameFrom:
    """`input`, `run-head` and `analysis-context` are three claims, not two."""

    def test_an_explicit_input_is_labelled_input(self) -> None:
        require_bash()
        match = re.search(
            r'if \[\[ -n "\$TESTED_SHA" \]\]; then\n'
            r'  TESTED_SHA_SOURCE="input"\n'
            r"else\n"
            r'  TESTED_SHA_SOURCE="run-head"\n'
            r"fi",
            SOURCE,
        )
        assert match is not None, "the tested-sha-source derivation moved"
        for given, expected in (("deadbeef" * 5, "input"), ("", "run-head")):
            script = (
                f'TESTED_SHA="{given}"\n' + match.group(0) + "\necho $TESTED_SHA_SOURCE"
            )
            result = subprocess.run(
                [bash_executable(), "-c", script],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.stdout.strip() == expected, (given, result.stdout)

    def test_the_analysis_context_label_is_still_the_third_state(self) -> None:
        # It is set in the provenance branch, which the two above do not
        # reach; asserting it here keeps the vocabulary complete.
        assert 'TESTED_SHA_SOURCE="analysis-context"' in SOURCE

    def test_the_declared_output_documents_all_three(self) -> None:
        import yaml

        action = yaml.safe_load(
            (RUN_SH.parent / "action.yml").read_text(encoding="utf-8")
        )
        described = action["outputs"]["tested-sha-source"]["description"]
        for state in ("input", "run-head", "analysis-context"):
            assert state in described, (state, described)
