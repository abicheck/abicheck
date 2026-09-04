"""``action/run.sh``'s own annotation renderer (CLI cleanup phase two, PR E).

The Action now emits GitHub Actions workflow-command annotations
(``::error``/``::warning``/``::notice``) itself, reading the persisted
``annotations`` array (report schema 2.43/2.44) off whichever JSON report
the run produced -- instead of asking ``abicheck compare --annotate`` to
render them to its own stderr. Driven through the real ``run.sh``: once
against a fake ``abicheck`` on ``$PATH`` whose own stdout is a
hand-crafted JSON report (proving the renderer's own filtering/gating
logic in isolation from the real CLI), and once against the real
``abicheck`` binary with a genuine breaking-change pair (the end-to-end
proof that a real persisted report's annotations actually reach the
Action's own log output).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_REAL_ABICHECK = shutil.which("abicheck")


def _run_compare(
    tmp_path: Path,
    env_extra: dict[str, str],
    *,
    stub_report: dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run compare mode; *stub_report* becomes the fake abicheck's stdout."""
    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    old_json.write_text("{}", encoding="utf-8")
    new_json.write_text("{}", encoding="utf-8")

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "abicheck"
    payload = json.dumps(stub_report if stub_report is not None else {"verdict": "COMPATIBLE"})
    stub.write_text(
        "#!/usr/bin/env bash\n" f"cat <<'STUBJSON'\n{payload}\nSTUBJSON\n" "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old_json),
        "INPUT_NEW_LIBRARY": str(new_json),
        "INPUT_FORMAT": "json",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        **env_extra,
    }
    return subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
    )


_REPORT_WITH_ANNOTATIONS = {
    "verdict": "BREAKING",
    "annotations": [
        {
            "level": "error",
            "annotation": "::error title=ABI Break::function foo removed",
            "always_visible": True,
        },
        {
            "level": "notice",
            "annotation": "::notice title=ABI Addition::function bar added",
            "always_visible": False,
        },
        {
            "level": "notice",
            "annotation": "::notice title=Not evaluated (contract)::baz",
            "always_visible": True,
        },
    ],
}


def _emitted_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Every exact stdout/stderr line starting with a workflow-command
    sigil (``::error``/``::warning``/``::notice``) -- what the renderer
    itself printed, as opposed to a byte range of the raw JSON dump that
    happens to contain the same text."""
    lines = (result.stdout + result.stderr).splitlines()
    return [ln for ln in lines if ln.startswith(("::error ", "::warning ", "::notice "))]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAnnotateRendererReadsThePersistedReport:
    def test_annotate_true_emits_the_always_visible_entries(
        self, tmp_path: Path
    ) -> None:
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "true"},
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        # Exact-line membership, not substring containment: FORMAT=json's
        # own primary output is the raw report text, which already contains
        # every annotation string embedded inside its JSON encoding --
        # a substring check can't tell "the renderer echoed this as its own
        # workflow-command line" apart from "this text happens to appear
        # somewhere in the JSON dump".
        lines = _emitted_lines(result)
        assert "::error title=ABI Break::function foo removed" in lines
        assert "::notice title=Not evaluated (contract)::baz" in lines
        # The opt-in addition notice must NOT appear without annotate-additions.
        assert "::notice title=ABI Addition::function bar added" not in lines

    def test_annotate_additions_includes_the_opt_in_notice(
        self, tmp_path: Path
    ) -> None:
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "true", "INPUT_ANNOTATE_ADDITIONS": "true"},
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "::notice title=ABI Addition::function bar added"
            in _emitted_lines(result)
        )

    def test_annotate_false_emits_nothing(self, tmp_path: Path) -> None:
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "false"},
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _emitted_lines(result) == []

    def test_annotate_additions_alone_explains_rather_than_silently_no_ops(
        self, tmp_path: Path
    ) -> None:
        """CodeRabbit review, fresh evidence: `annotate-additions: true`
        with `annotate` left at its default `false` used to be a hard CLI
        usage error (`--annotate-additions requires --annotate`, removed
        along with the flags). An Action input has no equivalent, but
        silently rendering nothing for this combination is still a
        surprising behaviour change -- it must say so.
        """
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE_ADDITIONS": "true"},
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        # No report annotation lines were rendered -- only the diagnostic.
        lines = _emitted_lines(result)
        assert "::error title=ABI Break::function foo removed" not in lines
        combined = result.stdout + result.stderr
        assert "::notice title=abicheck annotate::" in combined
        assert "annotate-additions is true but annotate is false" in combined

    def test_release_shape_flattens_across_libraries(self, tmp_path: Path) -> None:
        release_report = {
            "verdict": "BREAKING",
            "libraries": [
                {
                    "library": "liba.so",
                    "annotations": [
                        {
                            "level": "error",
                            "annotation": "::error title=ABI Break::liba broke",
                            "always_visible": True,
                        }
                    ],
                },
                {
                    "library": "libb.so",
                    "annotations": [
                        {
                            "level": "warning",
                            "annotation": "::warning title=API Break::libb warned",
                            "always_visible": True,
                        }
                    ],
                },
            ],
        }
        result = _run_compare(
            tmp_path, {"INPUT_ANNOTATE": "true"}, stub_report=release_report
        )
        assert result.returncode == 0, result.stdout + result.stderr
        lines = _emitted_lines(result)
        assert "::error title=ABI Break::liba broke" in lines
        assert "::warning title=API Break::libb warned" in lines

    def test_no_annotations_key_is_a_quiet_no_op(self, tmp_path: Path) -> None:
        # A scan-shaped (or pre-2.43) report carries no `annotations` at
        # all -- must not error, just print nothing.
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "true"},
            stub_report={"verdict": "COMPATIBLE"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _emitted_lines(result) == []

    def test_a_malformed_annotation_string_is_never_echoed(self, tmp_path: Path) -> None:
        """Codex review, fresh evidence: if `_json_report_src` ever resolves
        to a report this invocation didn't itself produce (e.g. a stale
        --output-file the abicheck run failed to overwrite), a crafted
        `annotation` string must not be echoed verbatim -- that would let
        an attacker smuggle an arbitrary GitHub Actions workflow command
        (``::stop-commands::``, a spoofed second command via an embedded
        newline, a `level` that disagrees with the printed prefix, ...)
        into the log. The renderer must validate the string's own shape
        (single line, `::{level} ` prefix agreeing with the typed `level`
        field) rather than trusting it.
        """
        malicious_report = {
            "verdict": "BREAKING",
            "annotations": [
                # Embedded newline smuggling a second, unrelated command.
                {
                    "level": "error",
                    "annotation": "::error title=x::ok\n::stop-commands::TOKEN",
                    "always_visible": True,
                },
                # `level` disagrees with the printed prefix.
                {
                    "level": "notice",
                    "annotation": "::error title=spoofed::not really a notice",
                    "always_visible": True,
                },
                # Not even workflow-command-shaped at all.
                {
                    "level": "error",
                    "annotation": "::stop-commands::TOKEN",
                    "always_visible": True,
                },
                # A genuinely well-formed entry, to prove the filter is
                # selective rather than suppressing everything.
                {
                    "level": "warning",
                    "annotation": "::warning title=real::a real finding",
                    "always_visible": True,
                },
            ],
        }
        result = _run_compare(
            tmp_path, {"INPUT_ANNOTATE": "true"}, stub_report=malicious_report
        )
        assert result.returncode == 0, result.stdout + result.stderr
        # _emitted_lines is the exact-line filter: it proves the renderer
        # itself never emitted the malicious lines as workflow commands,
        # independent of the raw JSON dump elsewhere in this FORMAT=json
        # run's own primary output legitimately containing the same
        # substring as inert JSON text.
        lines = _emitted_lines(result)
        assert lines == ["::warning title=real::a real finding"]

    def test_non_json_write_target_emits_a_diagnostic_not_silence(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #798: when the primary FORMAT isn't json AND the
        caller's own extra-args ``--write`` targets a non-json format
        (markdown/junit/sarif/html/review), there is genuinely no JSON
        report anywhere for this run -- unlike the ``json=`` case, nothing
        can be discovered. This must say so explicitly rather than
        silently emitting nothing.
        """
        result = _run_compare(
            tmp_path,
            {
                "INPUT_FORMAT": "markdown",
                "INPUT_ANNOTATE": "true",
                "INPUT_EXTRA_ARGS": f"--write markdown={tmp_path / 'out.md'}",
            },
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        # The diagnostic itself is a real ::notice workflow command (so it
        # shows up in the Actions log), but none of the *report's own*
        # annotation entries were ever discovered/rendered.
        assert "::error title=ABI Break::function foo removed" not in _emitted_lines(
            result
        )
        combined = result.stdout + result.stderr
        assert "::notice title=abicheck annotate::" in combined
        assert "no JSON report is available" in combined

    def test_effective_format_override_still_emits_the_diagnostic(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #998, fresh evidence: `format: json` overridden
        by `extra-args: --format text --write markdown=...` really does
        leave no JSON report anywhere (`_json_report_src` correctly finds
        nothing), but the notice above used to gate on the *nominal*
        `$FORMAT` -- which still read "json" -- and so stayed silent about
        exactly the situation it exists to explain. Same scenario as
        `test_non_json_write_target_emits_a_diagnostic_not_silence` above,
        with the override direction reversed.
        """
        result = _run_compare(
            tmp_path,
            {
                "INPUT_FORMAT": "json",
                "INPUT_ANNOTATE": "true",
                "INPUT_EXTRA_ARGS": f"--format text --write markdown={tmp_path / 'out.md'}",
            },
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::error title=ABI Break::function foo removed" not in _emitted_lines(
            result
        )
        combined = result.stdout + result.stderr
        assert "::notice title=abicheck annotate::" in combined
        assert "no JSON report is available" in combined

    def test_discovers_a_user_supplied_write_json_path(self, tmp_path: Path) -> None:
        """Codex review, PR #798: when the primary FORMAT isn't json and the
        caller's own extra-args already carries ``--write json=PATH``, the
        Action's internal ``--write json=$PR_JSON`` injection is correctly
        suppressed (``_extra_args_has_write_flag``) -- which used to leave
        ``_json_report_src``/the annotate renderer with no JSON source at
        all, so ``annotate: true`` silently emitted nothing even though the
        user's own ``--write`` destination held a perfectly good report.
        ``_extra_args_write_json_path`` now recovers that path directly.
        """
        old_json = tmp_path / "old.json"
        new_json = tmp_path / "new.json"
        old_json.write_text("{}", encoding="utf-8")
        new_json.write_text("{}", encoding="utf-8")
        write_path = tmp_path / "mine.json"

        # A real --write-capable stub: unlike _run_compare's fixed stub,
        # this one actually honors `--write json=PATH` by writing the
        # payload there too, so the renderer has something real to
        # discover -- not just a plausible-looking argv.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        stub = fake_bin / "abicheck"
        payload = json.dumps(_REPORT_WITH_ANNOTATIONS)
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "for arg in \"$@\"; do\n"
            '  case "$arg" in\n'
            "    --write) _want_next=1; continue ;;\n"
            "    json=*) if [[ \"${_want_next:-0}\" == 1 ]]; then\n"
            f"      cat > \"${{arg#json=}}\" <<'STUBJSON'\n{payload}\nSTUBJSON\n"
            "    fi ;;\n"
            "  esac\n"
            "  _want_next=0\n"
            "done\n"
            "echo markdown-primary-output\n"
            "exit 4\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

        base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        env = {
            **base_env,
            "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(old_json),
            "INPUT_NEW_LIBRARY": str(new_json),
            "INPUT_FORMAT": "markdown",
            "INPUT_ADD_JOB_SUMMARY": "false",
            "INPUT_PR_COMMENT": "false",
            "INPUT_ANNOTATE": "true",
            "INPUT_EXTRA_ARGS": f"--write json={write_path}",
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        }
        result = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
        )
        assert write_path.is_file(), result.stdout + result.stderr
        lines = _emitted_lines(result)
        assert "::error title=ABI Break::function foo removed" in lines

    def test_a_stale_pre_existing_write_destination_is_neither_trusted_nor_deleted(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence, two rounds: (1) a stale file
        already at the `--write json=PATH` destination before this
        invocation, left untouched because the stub fails before writing,
        must not be read as if it were this run's own report (staleness);
        (2) the fix for that must not delete the file to "prove"
        freshness either -- `output-file`/`--write`'s PATH is still just an
        `INPUT_*` value, and unconditionally unlinking a user-controlled
        path before the invocation is validated risks destroying a real
        *input* the comparison needed, if that path happens to coincide
        with one (a first fix attempt did exactly this and was reverted).
        The actual fix (a non-destructive mtime/size fingerprint) must
        satisfy both at once: the stale content survives on disk,
        unmodified, and is never rendered as this run's annotations.
        """
        old_json = tmp_path / "old.json"
        new_json = tmp_path / "new.json"
        old_json.write_text("{}", encoding="utf-8")
        new_json.write_text("{}", encoding="utf-8")
        write_path = tmp_path / "mine.json"
        stale_content = json.dumps({"verdict": "STALE", "sentinel": "pre-existing"})
        write_path.write_text(stale_content, encoding="utf-8")

        # A stub that fails WITHOUT touching write_path at all -- mirrors
        # a real `abicheck` crash before it ever reaches its own --write.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        stub = fake_bin / "abicheck"
        stub.write_text(
            "#!/usr/bin/env bash\necho boom >&2\nexit 1\n", encoding="utf-8"
        )
        stub.chmod(0o755)

        base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        env = {
            **base_env,
            "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(old_json),
            "INPUT_NEW_LIBRARY": str(new_json),
            "INPUT_FORMAT": "markdown",
            "INPUT_ADD_JOB_SUMMARY": "false",
            "INPUT_PR_COMMENT": "false",
            "INPUT_ANNOTATE": "true",
            "INPUT_EXTRA_ARGS": f"--write json={write_path}",
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        }
        result = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
        )
        # Not deleted, not modified -- the non-destructive half of the fix.
        assert write_path.is_file(), "the pre-existing file must survive"
        assert write_path.read_text(encoding="utf-8") == stale_content
        # Not trusted as this run's report -- the staleness half.
        assert _emitted_lines(result) == []


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or _REAL_ABICHECK is None
    or not RUN_SH.is_file(),
    reason="Linux-only (matches test_action_baseline.py's own real-abicheck "
    "end-to-end precedent) with a real abicheck binary and action/run.sh",
)
class TestRealAbicheckAnnotationsReachTheActionLog:
    """The genuinely end-to-end proof: real `abicheck`, a real breaking
    change, real persisted annotations, actually echoed by the real
    `run.sh` -- not a stub that could pass regardless of what the
    renderer's own logic does wrong.
    """

    def test_a_real_breaking_change_is_annotated(self, tmp_path: Path) -> None:
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        def _fn(name: str, mangled: str) -> Function:
            return Function(
                name=name, mangled=mangled, return_type="void",
                visibility=Visibility.PUBLIC,
            )

        old_snap = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[_fn("foo", "_Z3foov"), _fn("bar", "_Z3barv")],
            from_headers=True,
        )
        new_snap = AbiSnapshot(
            library="libfoo.so", version="2.0",
            functions=[_fn("foo", "_Z3foov")],
            from_headers=True,
        )
        old_json = tmp_path / "old.json"
        new_json = tmp_path / "new.json"
        old_json.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_json.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        env = {
            **base_env,
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(old_json),
            "INPUT_NEW_LIBRARY": str(new_json),
            "INPUT_FORMAT": "markdown",
            "INPUT_ADD_JOB_SUMMARY": "false",
            "INPUT_PR_COMMENT": "false",
            "INPUT_ANNOTATE": "true",
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        }
        # mutmut's trampoline reads pyproject.toml from the subprocess cwd.
        # Keep action inputs in tmp_path, but run from the copied repo root so
        # its mutation config remains available during clean-test collection.
        result = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=RUN_SH.parent.parent,
            check=False,
        )
        combined = result.stdout + result.stderr
        assert "::error" in combined, combined
        assert "bar" in combined, combined
