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

"""Codex review finding (P1, fresh evidence, PR #1222, commit 948fed4f8a):
``actions/check-target/action.yml``'s "Generate assurance-overlay config"
step forwards its own generated overlay as ``build-config`` to the nested
"Run analysis" step -- but that step's ``extra-args`` used to always
forward the caller's raw ``inputs.extra-args`` unchanged. A caller who
names a config via ``extra-args: --config <path>`` instead of the
``build-config`` input (both are otherwise-supported, independent ways to
name a project config) would then reach the nested Action with BOTH an
overlay ``--config`` (from ``build-config``) and their own raw ``--config``
(from ``extra-args``) -- ``action/run.sh``'s ``_cmd_has_config_flag &&
_extra_args_has_config_flag`` guard rejects exactly that combination as two
irreconcilable configs (Click would otherwise silently keep only the last
one), so ``analysis-assurance-complete: true`` broke a check that worked
fine before it was enabled.

Fixed by extracting a ``--config``/``--config=<path>`` occurrence out of
``extra-args`` (mirroring ``action/run.sh``'s own ``_extra_args_options``
tokenizer closely enough to agree with it on what counts as a real
``--config`` FLAG, not some other value-taking option's own literal value),
using it as the SAME merge base the explicit-``build-config`` branch
already builds from when no explicit ``build-config`` was given, and
stripping the consumed pair out of what this step forwards onward as its
own ``effective-extra-args`` output -- so the merged config reaches the
nested Action exactly once. An explicit ``build-config`` input still wins
outright: that combination (``build-config`` + ``extra-args --config``)
keeps its existing, pre-established rejection (action/run.sh's own
documented "use build-config instead" guidance) rather than silently
picking a winner.

Split out as a new sibling module rather than folding into
``tests/test_reusable_workflows_require_complete_analysis.py`` (already at
its ``architecture/debt.yaml`` test-file size cap) or
``tests/test_action_config_overlay.py`` (also effectively at cap) -- the
same "move responsibility, don't trim to fit" convention the root
``AGENTS.md`` states, and the same reason
``tests/test_reusable_workflows_assurance_overlay_compile_context.py``/
``tests/test_reusable_workflows_assurance_overlay_parse_safety.py`` exist
as their own modules. Shares the same execution helpers
(``tests/_assurance_overlay_exec.py``) those siblings use, unchanged.
"""

from __future__ import annotations

from pathlib import Path

from _assurance_overlay_exec import _run_overlay, _written_overlay
from _workflow_exec import make_workspace, run_step


class TestAssuranceOverlayMergesExtraArgsConfig:
    """No explicit ``build-config``: a caller's own ``extra-args: --config
    <path>`` becomes the overlay's merge base, exactly as if it had been
    named via ``build-config`` instead."""

    def test_extra_args_config_is_used_as_merge_base(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--config custom.yml"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {
            "targets": {},
            "assurance": {"require_complete": True},
        }

    def test_consumed_config_is_stripped_from_effective_extra_args(
        self, tmp_path: Path
    ) -> None:
        """The nested "Run analysis" step must never ALSO see the raw
        ``--config custom.yml`` -- it has already been folded into the
        overlay `build-config` above, and forwarding it again would
        recreate the exact two-``--config`` collision this fix closes."""
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--config custom.yml"},
        )
        assert result.returncode == 0, result.stderr
        assert result.outputs["effective-extra-args"] == ""

    def test_equals_form_is_also_recognized(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--config=custom.yml"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {}
        assert result.outputs["effective-extra-args"] == ""

    def test_other_extra_args_survive_around_the_removed_config_pair(
        self, tmp_path: Path
    ) -> None:
        """Only the ``--config``/its value is removed -- every other token,
        before and after it, is preserved in order."""
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "EXTRA_ARGS": "--severity-preset strict --config custom.yml --suppress ID1",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {}
        assert (
            result.outputs["effective-extra-args"]
            == "--severity-preset strict --suppress ID1"
        )

    def test_repeated_config_keeps_the_last_one(self, tmp_path: Path) -> None:
        """Mirrors Click's own "keeps only the last one" semantics for a
        repeated scalar option -- the same behavior extra-args would have
        had if forwarded raw with no synthesized `build-config` overlay at
        all."""
        workspace = make_workspace(tmp_path)
        (workspace / "first.yml").write_text("targets: {a: {}}\n", encoding="utf-8")
        (workspace / "second.yml").write_text("targets: {b: {}}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "EXTRA_ARGS": "--config first.yml --config second.yml",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {"b": {}}
        assert result.outputs["effective-extra-args"] == ""

    def test_a_config_looking_value_of_another_option_is_not_misread(
        self, tmp_path: Path
    ) -> None:
        """`--policy --config` here means `--policy`'s own (unusually
        literal) value is the string "--config" -- not a real `--config`
        flag. Getting this wrong would silently swallow the next real
        token (or a whole unrelated file) as a bogus merge base."""
        workspace = make_workspace(tmp_path)
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--policy --config"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        # No real --config token was found, so no merge base beyond the
        # (empty, undiscovered) default -- just the overlay's own field.
        assert written == {"assurance": {"require_complete": True}}
        assert result.outputs["effective-extra-args"] == "--policy --config"


class TestAssuranceOverlayExpandsShortOptionClusters:
    """Codex review (P2), fresh evidence, PR #1222: a Click-style clustered
    short-option token (`-vH` for `-v -H`) must be expanded BEFORE this
    tokenizer's pending-value/``--config``-recognition logic runs, so a
    header path literally spelled ``--config`` following such a cluster is
    correctly read as ``-H``'s own VALUE -- never mistaken for a real
    ``--config`` flag occurrence.

    Confirmed directly against the installed CLI before writing this fix:
    ``compare --help-all`` documents ``-v`` as a boolean flag and
    ``-H [old=|new=]PATH`` as a value-taking short option, and
    ``compare old.so new.so -vH --config`` (with a file literally named
    ``--config``) is genuinely ACCEPTED -- ``-H``'s value is the string
    ``--config``, a legitimate header-path argument, not a second flag.
    Before this fix, this tokenizer (which never expanded clusters) read
    the whole ``-vH`` token as one opaque, unrecognized flag and then read
    the following bare ``--config`` as a real, trailing, valueless
    ``--config`` occurrence -- which
    ``TestAssuranceOverlayRejectsValuelessConfigOccurrence`` below's own
    guard then rejects outright, turning argv the real CLI accepts into a
    spurious step failure."""

    def test_cluster_before_config_shaped_value_is_not_misread(
        self, tmp_path: Path
    ) -> None:
        """The exact reported repro: ``-vH --config``, where ``--config``
        is meant as ``-H``'s own literal path VALUE (a header file
        literally named ``--config``), not a real ``--config`` flag.
        Overlay generation must succeed, with no merge-base config
        extracted, and the untouched raw ``extra-args`` forwarded onward
        unchanged (this step's own pass-through behavior for extra-args
        that name no real ``--config`` flag)."""
        workspace = make_workspace(tmp_path)
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "-vH --config"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}
        assert result.outputs["effective-extra-args"] == "-vH --config"

    def test_real_config_flag_survives_alongside_an_unrelated_cluster(
        self, tmp_path: Path
    ) -> None:
        """A genuine ``--config real.yml`` occurring alongside an
        unrelated short option (``-v``, itself too short to be a
        multi-character cluster) must still be recognized and extracted
        exactly as before -- the cluster-expansion fix must not regress
        ordinary, non-clustered ``--config`` extraction."""
        workspace = make_workspace(tmp_path)
        (workspace / "real.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "-v --config real.yml"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {}
        assert result.outputs["effective-extra-args"] == "-v"

    def test_cluster_with_a_genuinely_separate_config_flag_after_it(
        self, tmp_path: Path
    ) -> None:
        """A cluster (``-vH somefile.h``) followed by a genuinely
        separate, real ``--config`` flag: ``-H``'s own value is
        ``somefile.h``, and the LATER ``--config real.yml`` is a real flag
        that must still be extracted -- proving cluster expansion and
        ``--config`` recognition compose correctly rather than only one of
        the two firing. The reconstructed ``effective-extra-args`` must
        show the cluster expanded into its constituent ``-v``/``-H``
        tokens (reconstruction only happens once a real config IS
        extracted -- see the `BASE_CONFIG`/`_ct_extra_args_config`
        branch)."""
        workspace = make_workspace(tmp_path)
        (workspace / "real.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "EXTRA_ARGS": "-vH somefile.h --config real.yml",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {}
        assert result.outputs["effective-extra-args"] == "-v -H somefile.h"


class TestAssuranceOverlayRejectsValuelessConfigOccurrence:
    """Codex review (P2), fresh evidence, PR #1222: a ``--config`` occurrence
    in ``extra-args`` with no value at all (a trailing bare ``--config``, or
    the ``=``-form with an empty value) is malformed usage the REAL CLI
    itself rejects with a Click "requires an argument"/"Invalid value"
    usage error (exit 64) -- confirmed directly against the installed CLI:

    ``compare --config good.yml --config`` ->
    ``Error: Option '--config' requires an argument.`` (exit 64)

    ``compare --config=`` ->
    ``Error: Invalid value for '--config': File '' does not exist.``
    (exit 64)

    Before this fix, ``_ct_extra_args_config`` retained an EARLIER, valid
    ``--config`` occurrence (or nothing) while silently discarding the
    trailing malformed one during reconstruction -- turning malformed input
    into a successful overlay-generation run instead of preserving the real
    CLI's own hard-error semantics. Both shapes must now fail this step
    loud, with a clear ``::error::`` annotation, rather than silently
    dropping the offending token and letting the nested run accept a
    generated overlay as if extra-args had never named a malformed
    ``--config`` at all."""

    def test_trailing_bare_config_after_an_earlier_valid_one_fails_loud(
        self, tmp_path: Path
    ) -> None:
        """The exact reported repro: TWO ``--config`` occurrences, where the
        first is valid and the final one is a bare, valueless trailing
        token. Retaining the first and silently discarding the second would
        make this step succeed where the real CLI errors out."""
        workspace = make_workspace(tmp_path)
        (workspace / "good.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--config good.yml --config"},
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "::error::" in combined
        assert "--config" in combined
        assert "config-path" not in result.outputs
        assert "effective-extra-args" not in result.outputs

    def test_lone_trailing_bare_config_fails_loud(self, tmp_path: Path) -> None:
        """The same malformed shape with no earlier valid occurrence to
        (wrongly) fall back to."""
        workspace = make_workspace(tmp_path)
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--severity-preset strict --config"},
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "::error::" in combined
        assert "config-path" not in result.outputs

    def test_equals_form_with_empty_value_fails_loud(self, tmp_path: Path) -> None:
        """``--config=`` (the ``=``-form with an EMPTY value) is just as
        malformed, and was previously swallowed silently by the generic
        ``--config=*`` branch's own "no value to merge" guard rather than
        being preserved for the real CLI to reject."""
        workspace = make_workspace(tmp_path)
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--config="},
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "::error::" in combined
        assert "config-path" not in result.outputs

    def test_equals_form_with_a_real_value_still_succeeds(
        self, tmp_path: Path
    ) -> None:
        """Sanity check alongside the two failure cases above: a REAL
        ``--config=<path>`` value must keep working exactly as before this
        fix -- only the EMPTY-value shape is malformed."""
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--config=custom.yml"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {}


class TestAssuranceOverlayHonorsEndOfOptionsMarker:
    """Codex review (P2), fresh evidence, PR #1222, commit 46e1a64c9c: a
    bare ``--`` end-of-options marker in ``extra-args`` ends option
    parsing for any Click-based CLI (including this repo's own ``compare``
    command) -- everything after it is a positional operand, never an
    option, no matter what it looks like. Confirmed directly against the
    installed CLI's own Click parser: a ``--config`` occurring after ``--``
    is not recognized as the ``--config`` option at all, so
    ``compare -- --config custom.yml`` fails with a REAL usage error
    (``Error: Got unexpected extra arguments (--config custom.yml)``,
    exit 64) -- not the option's own "requires an argument"/"Invalid
    value" errors the earlier valueless-``--config`` fixes above defend.

    Before this fix, this tokenizer's scan didn't track ``--`` at all, so
    it kept recognizing and extracting a ``--config``/``--config=...``
    occurrence appearing AFTER a literal ``--`` token exactly as if the
    marker weren't there -- reconstructing the remaining args as just
    ``--`` and silently removing the two malformed operands. That let
    ``analysis-assurance-complete: true`` turn a real exit-64 usage error
    into a successful run, the same "malformed input quietly becomes
    success" gap the earlier trailing-``--config``/``--config=`` fixes in
    this file close for the OTHER malformed shapes."""

    def test_config_after_end_of_options_marker_is_not_extracted(
        self, tmp_path: Path
    ) -> None:
        """``-- --config custom.yml``: the ``--config custom.yml`` pair is
        NOT a real option occurrence -- it's two positional operands after
        ``--``. No config may be extracted, and the entire remaining
        sequence (the marker and everything after it) must pass through
        unchanged so the nested CLI sees the identical malformed input and
        produces the identical real exit-64 error."""
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "-- --config custom.yml"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        # No real --config token was recognized, so no merge base beyond
        # the overlay's own field.
        assert written == {"assurance": {"require_complete": True}}
        assert result.outputs["effective-extra-args"] == "-- --config custom.yml"

    def test_equals_form_after_end_of_options_marker_is_not_extracted(
        self, tmp_path: Path
    ) -> None:
        """Same shape, ``=``-form: ``-- --config=custom.yml``."""
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "-- --config=custom.yml"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}
        assert result.outputs["effective-extra-args"] == "-- --config=custom.yml"

    def test_config_before_end_of_options_marker_is_still_extracted(
        self, tmp_path: Path
    ) -> None:
        """Positive/negative control: the marker's POSITION matters, not
        merely its presence somewhere in ``extra-args``. A real
        ``--config`` occurring BEFORE ``--`` is still a genuine option and
        must still be extracted exactly as the earlier tests in this file
        establish -- only tokens AFTER the marker become immune to
        recognition. Everything from ``--`` onward (the marker plus the
        unrelated trailing operand) is passed through unchanged."""
        workspace = make_workspace(tmp_path)
        (workspace / "good.yml").write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--config good.yml -- something"},
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {}
        assert result.outputs["effective-extra-args"] == "-- something"


class TestAssuranceOverlayLeavesExtraArgsAloneWhenUnaffected:
    def test_extra_args_without_config_is_forwarded_unchanged(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        result = _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "EXTRA_ARGS": "--severity-preset strict"},
        )
        assert result.returncode == 0, result.stderr
        assert result.outputs["effective-extra-args"] == "--severity-preset strict"

    def test_empty_extra_args_stays_empty(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        result = _run_overlay(workspace, {"BASE_CONFIG": "", "EXTRA_ARGS": ""})
        assert result.returncode == 0, result.stderr
        assert result.outputs["effective-extra-args"] == ""


class TestAssuranceOverlayExplicitBuildConfigWinsOverExtraArgsConfig:
    """An explicit ``build-config`` input is a more specific, deliberate
    operator signal than ``extra-args --config`` -- this step leaves BOTH
    untouched in that case (using ``build-config`` as the merge base, same
    as before this fix, and forwarding ``extra-args`` raw), preserving
    action/run.sh's own pre-existing "use build-config instead" rejection
    of that combination rather than silently picking a winner or merging a
    third config source in."""

    def test_build_config_input_is_the_merge_base_not_extra_args_config(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        explicit_config = workspace / "explicit.yml"
        explicit_config.write_text("targets: {explicit: {}}\n", encoding="utf-8")
        (workspace / "other.yml").write_text("targets: {other: {}}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": str(explicit_config),
                "EXTRA_ARGS": "--config other.yml",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["targets"] == {"explicit": {}}

    def test_extra_args_is_forwarded_unchanged_alongside_explicit_build_config(
        self, tmp_path: Path
    ) -> None:
        """The raw `--config other.yml` is left in `extra-args` -- this
        step does not attempt to strip or merge it when `build-config` was
        already given explicitly, matching pre-fix behavior for this
        specific combination exactly (still action/run.sh's own downstream
        rejection to handle, not this step's job to silently resolve)."""
        workspace = make_workspace(tmp_path)
        explicit_config = workspace / "explicit.yml"
        explicit_config.write_text("targets: {}\n", encoding="utf-8")
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": str(explicit_config),
                "EXTRA_ARGS": "--config other.yml",
            },
        )
        assert result.returncode == 0, result.stderr
        assert result.outputs["effective-extra-args"] == "--config other.yml"


class TestStepResultOutputsParsesMultilineRecords:
    """``effective-extra-args`` above is written via GitHub's documented
    ``name<<DELIMITER`` / body / ``DELIMITER`` multiline ``$GITHUB_OUTPUT``
    form rather than a plain ``key=value`` line, since ``extra-args`` can
    legitimately contain a raw newline (a YAML literal-block ``|`` value --
    see ``action/run.sh``'s own ``_extra_args_options`` docstring). Before
    this fix, ``tests/_workflow_exec.py``'s own ``StepResult.outputs``
    only understood a plain ``key=value`` line, so a real multiline record
    was invisible to it (its delimiter/body lines carry no ``=`` in
    general) -- ``result.outputs["effective-extra-args"]`` would have
    raised ``KeyError`` rather than reading the real value, for every one
    of the tests above. This class pins the parser's own general contract
    directly, independent of any one producer step."""

    def test_multiline_record_is_parsed(self, tmp_path: Path) -> None:
        script = (
            "{\n"
            '  echo "greeting<<EOF_DELIM"\n'
            '  echo "hello"\n'
            '  echo "world"\n'
            '  echo "EOF_DELIM"\n'
            '} >> "$GITHUB_OUTPUT"\n'
        )
        result = run_step({"run": script}, workspace=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.outputs["greeting"] == "hello\nworld"

    def test_multiline_record_with_empty_body_is_parsed_as_empty_string(
        self, tmp_path: Path
    ) -> None:
        script = (
            '{\n  echo "greeting<<EOF_DELIM"\n  echo "EOF_DELIM"\n} '
            '>> "$GITHUB_OUTPUT"\n'
        )
        result = run_step({"run": script}, workspace=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.outputs["greeting"] == ""

    def test_multiline_record_coexists_with_a_plain_key_value_line(
        self, tmp_path: Path
    ) -> None:
        script = (
            "{\n"
            '  echo "before=1"\n'
            '  echo "greeting<<EOF_DELIM"\n'
            '  echo "hello"\n'
            '  echo "EOF_DELIM"\n'
            '  echo "after=2"\n'
            '} >> "$GITHUB_OUTPUT"\n'
        )
        result = run_step({"run": script}, workspace=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.outputs["before"] == "1"
        assert result.outputs["greeting"] == "hello"
        assert result.outputs["after"] == "2"

    def test_body_line_containing_equals_is_not_misread_as_a_new_record(
        self, tmp_path: Path
    ) -> None:
        """A body line that happens to contain "=" (e.g. a real value like
        "--severity-preset=strict") must stay part of the multiline
        record's value, not get mis-split as its own key=value pair."""
        script = (
            "{\n"
            '  echo "extra-args<<EOF_DELIM"\n'
            '  echo "--severity-preset=strict"\n'
            '  echo "EOF_DELIM"\n'
            '} >> "$GITHUB_OUTPUT"\n'
        )
        result = run_step({"run": script}, workspace=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.outputs["extra-args"] == "--severity-preset=strict"
