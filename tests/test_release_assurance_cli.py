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

"""ADR-071 through the real CLI, on a real multi-library bundle.

`AGENTS.md`'s "Validate the user-facing result" rule: the property suite in
``tests/test_release_assurance_properties.py`` states the fold's contract
over generated member sets, but a fold nothing wires up proves nothing. These
tests drive ``abicheck compare`` over two real directories of real ELF shared
libraries and read the rendered report and the process exit code, which is
the only place the cardinality-agreement invariant can actually be checked:
the scalar path and the release path are two different code paths, and the
property suite can only compare the release fold against a *restatement* of
the scalar rule.

Requires a working ``gcc`` (marked ``integration``), since a synthetic
``AbiSnapshot`` pair could not exercise the release fan-out's own per-member
recording at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    # The fixture links real ELF shared libraries with `-Wl,-soname`, a GNU ld
    # flag Apple's linker rejects outright (it spells the same concept
    # `-install_name`), so every test here errored at *setup* on the macOS
    # integration lane. Guarded rather than made portable: what these tests
    # validate is the release fan-out's per-member assurance recording over a
    # real multi-library ELF bundle with real DWARF, and a Mach-O fixture would
    # be a different subject, not the same one built differently. Same guard,
    # same reason, as `test_cli_compare_bundle_facts.TestCompareOldBundleFacts`
    # and its siblings. The fold's own contract is platform-independent and is
    # stated in `tests/test_release_assurance_properties.py`, which runs
    # everywhere.
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="Uses the GNU ld flag -Wl,-soname; ELF/Linux-only bundle analysis.",
    ),
]

_LIBS = ("core", "thread", "dpc")


def _have_gcc() -> bool:
    return shutil.which("gcc") is not None


def _write_sources(root: Path, *, extra_field: bool) -> Path:
    """A small multi-library toolkit's public headers + sources under *root*.

    *extra_field* adds a trailing member to ``core``'s public struct -- a real
    ABI break only L2 header evidence can see, so the same fixture exercises
    both "assurance complete" and "a real finding is still reported".
    """
    inc = root / "include"
    inc.mkdir(parents=True, exist_ok=True)
    for lib in _LIBS:
        field = " int threads;" if (extra_field and lib == "core") else ""
        (inc / f"toolkit_{lib}.h").write_text(
            f"#ifndef TOOLKIT_{lib.upper()}_H\n"
            f"#define TOOLKIT_{lib.upper()}_H\n"
            f"struct toolkit_{lib}_cfg {{ int n; double scale;{field} }};\n"
            f"int toolkit_{lib}_compute(int n);\n"
            f"int toolkit_{lib}_configure(const struct toolkit_{lib}_cfg *cfg);\n"
            "#endif\n"
        )
        (root / f"{lib}.c").write_text(
            f'#include "toolkit_{lib}.h"\n'
            f"int toolkit_{lib}_compute(int n) {{ return n + 1; }}\n"
            f"int toolkit_{lib}_configure(const struct toolkit_{lib}_cfg *cfg)"
            " { return cfg ? cfg->n : 0; }\n"
        )
    return inc


def _build(
    root: Path,
    out: Path,
    inc: Path,
    libs: tuple[str, ...] = _LIBS,
    *,
    debug: bool = True,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for lib in libs:
        subprocess.run(
            [
                "gcc",
                *(["-g"] if debug else []),
                "-shared",
                "-fPIC",
                f"-I{inc}",
                "-Wl,-soname",
                f"libtoolkit_{lib}.so.1",
                str(root / f"{lib}.c"),
                "-o",
                str(out / f"libtoolkit_{lib}.so.1"),
            ],
            check=True,
            capture_output=True,
        )


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Two real release directories plus both sides' public headers."""
    if not _have_gcc():
        pytest.skip("gcc is required to build the bundle fixture")
    base = tmp_path_factory.mktemp("release_assurance")
    old_src, new_src = base / "old", base / "new"
    old_inc = _write_sources(old_src, extra_field=False)
    new_inc = _write_sources(new_src, extra_field=True)
    _build(old_src, base / "v1", old_inc)
    _build(new_src, base / "v2", new_inc)
    # The same libraries as v1, built WITHOUT -g. Compared against v1 this is
    # a release with no ABI change at all and yet a genuinely incomplete
    # analysis on every member (asymmetric DWARF context: the layout
    # detectors never examined the debug-info-less side), which is the only
    # shape in which the assurance axis is the sole thing that can decide the
    # exit code. Built from the OLD sources so nothing else differs.
    _build(old_src, base / "v1_nodebug", old_inc, debug=False)
    # The NEW sources again, but with exactly one member (`thread`) built
    # without `-g`. This is ADR-071 D3's own worked example of a per-member
    # shortfall -- "every member compared, one member's DWARF missing => scope
    # complete, assurance partial" -- and it is the only fixture shape in this
    # module that isolates a shortfall to a *single* member while its siblings
    # stay `complete` and `core`'s real ABI break still decides the exit code.
    #
    # An earlier revision tried to manufacture the same shape by passing
    # `--header` for two of the three libraries and expecting the third to read
    # `partial`. That premise was wrong: `AnalysisAssurance` measures whether
    # the evidence each comparison *did* run on was complete and symmetric, not
    # whether a member's evidence tier matches its siblings'. Both sides of the
    # header-less member were symmetric `-g` builds, so `header_context_status`
    # and `dwarf_context_status` were legitimately `clean` and the member
    # legitimately `complete` -- on the scalar path too, verified directly, so
    # nothing about the release fold was implicated.
    _build(new_src, base / "v2_one_nodebug", new_inc, ("core", "dpc"))
    _build(new_src, base / "v2_one_nodebug", new_inc, ("thread",), debug=False)
    # A one-member release carved out of the same build, for the cardinality
    # invariant -- the same bytes, so any difference is the fold's, not the
    # fixture's.
    (base / "one_v1").mkdir()
    (base / "one_v2").mkdir()
    shutil.copy(base / "v1" / "libtoolkit_core.so.1", base / "one_v1")
    shutil.copy(base / "v2" / "libtoolkit_core.so.1", base / "one_v2")
    cfg = base / "require.yml"
    cfg.write_text("assurance:\n  require_complete: true\n")
    return {
        "base": base,
        "v1": base / "v1",
        "v2": base / "v2",
        "v1_nodebug": base / "v1_nodebug",
        "v2_one_nodebug": base / "v2_one_nodebug",
        "one_v1": base / "one_v1",
        "one_v2": base / "one_v2",
        "old_inc": old_inc,
        "new_inc": new_inc,
        "cfg": cfg,
    }


def _compare(
    *args: str,
    headers_for: tuple[str, ...] = (),
    bundle: dict[str, Path],
    require: bool = True,
    fmt: str = "json",
) -> subprocess.CompletedProcess[str]:
    """One real ``abicheck compare`` run, as a subprocess.

    A subprocess rather than ``CliRunner``: this test's whole subject is the
    *process exit code* the orthogonal axis floors, and an in-process runner
    intercepting ``sys.exit`` is one indirection away from the thing being
    asserted.
    """
    cmd = [sys.executable, "-m", "abicheck", "compare", *args, "-o", f"{fmt}=-"]
    for lib in headers_for:
        cmd += [
            "--header",
            f"old={bundle['old_inc'] / f'toolkit_{lib}.h'}",
            "--header",
            f"new={bundle['new_inc'] / f'toolkit_{lib}.h'}",
        ]
    if require:
        cmd += ["--config", str(bundle["cfg"])]
    return subprocess.run(cmd, capture_output=True, text=True)


class TestReleaseAssuranceThroughTheCli:
    def test_full_header_evidence_reports_complete_and_does_not_floor(
        self, bundle: dict[str, Path]
    ) -> None:
        """Every member analysed completely: the axis contributes 0 and the
        real break in ``core`` still decides the exit code.

        This is the end-to-end case the four removed guards used to make
        unreachable -- a single multi-library bundle L2 analysis under
        ``assurance.require_complete: true``.
        """
        run = _compare(
            str(bundle["v1"]),
            str(bundle["v2"]),
            headers_for=_LIBS,
            bundle=bundle,
        )
        assert run.returncode == 4, run.stderr
        doc = json.loads(run.stdout)
        aa = doc["analysis_assurance"]
        assert aa["status"] == "complete"
        assert aa["member_count"] == len(_LIBS)
        assert aa["incomplete_member_count"] == 0
        assert aa["exit_contribution"] == 0
        assert aa["incomplete_members"] == []
        # The compatibility gate decided, not this axis.
        assert doc["exit"]["code"] == 4
        assert doc["exit"]["analysis_assurance_contribution"] == 0
        assert "analysis_assurance" not in doc["exit"]["reasons"]

    def test_a_single_member_shortfall_is_not_masked_by_complete_siblings(
        self, bundle: dict[str, Path]
    ) -> None:
        """One of three members built without ``-g``: it is ``partial`` and the
        release's aggregate follows it, naming that member.

        The siblings are `complete` on the same run, which is what makes this
        the masking check rather than a restatement of
        ``test_the_axis_floors_an_otherwise_clean_release_to_one`` (where every
        member is short of evidence and any fold that looked at only one member
        would still pass).
        """
        run = _compare(
            str(bundle["v1"]),
            str(bundle["v2_one_nodebug"]),
            bundle=bundle,
        )
        doc = json.loads(run.stdout)
        aa = doc["analysis_assurance"]
        assert aa["status"] != "complete"
        assert aa["incomplete_member_count"] == 1
        assert aa["exit_contribution"] == 1
        (short,) = aa["incomplete_members"]
        assert "thread" in short["library"]
        assert short["notes"], "a shortfall must say why, not only that"
        per_lib = {
            lib["library"]: lib.get("analysis_assurance_status")
            for lib in doc["libraries"]
        }
        assert sorted(per_lib.values()) == ["complete", "complete", "partial"]
        # Preserved for explainability, never lowering the compatibility gate.
        assert doc["exit"]["code"] == 4
        assert doc["exit"]["analysis_assurance_contribution"] == 1

    def test_the_axis_floors_an_otherwise_clean_release_to_one(
        self, bundle: dict[str, Path]
    ) -> None:
        """No ABI change and no headers, but one side carries no DWARF: the
        analysis is incomplete on every member while nothing breaks, so the
        assurance axis is the only thing that can decide the exit.

        Note the deliberately *different* negative control used elsewhere in
        this class: comparing a ``-g`` build against itself with no headers
        reports ``complete`` (both sides symmetric, nothing unexamined), so it
        would prove nothing here -- which is exactly why this case strips the
        debug info on one side instead."""
        run = _compare(str(bundle["v1"]), str(bundle["v1_nodebug"]), bundle=bundle)
        assert run.returncode == 1, run.stdout + run.stderr
        doc = json.loads(run.stdout)
        assert doc["exit"]["code"] == 1
        assert doc["exit"]["reasons"] == ["analysis_assurance"]
        assert doc["analysis_assurance"]["exit_contribution"] == 1

    def test_a_one_member_release_agrees_with_the_scalar_path(
        self, bundle: dict[str, Path]
    ) -> None:
        """ADR-071 D1, on the real pair of code paths.

        The property suite can only check the release fold against a
        *restatement* of the scalar rule; this runs both real commands on the
        same two files and compares the exit code and the axis contribution
        they actually produce.
        """
        release = _compare(str(bundle["one_v1"]), str(bundle["one_v2"]), bundle=bundle)
        scalar = _compare(
            str(bundle["one_v1"] / "libtoolkit_core.so.1"),
            str(bundle["one_v2"] / "libtoolkit_core.so.1"),
            bundle=bundle,
        )
        assert release.returncode == scalar.returncode, (release.stdout, scalar.stdout)
        rel_doc, sca_doc = json.loads(release.stdout), json.loads(scalar.stdout)
        assert (
            rel_doc["exit"]["analysis_assurance_contribution"]
            == sca_doc["exit"]["analysis_assurance_contribution"]
        )
        assert rel_doc["exit"]["reasons"] == sca_doc["exit"]["reasons"]
        # And the status each reports for that one library is the same.
        assert (
            rel_doc["analysis_assurance"]["status"]
            == sca_doc["analysis_assurance"]["status"]
        )

    def test_without_the_setting_the_report_and_exit_are_unchanged(
        self, bundle: dict[str, Path]
    ) -> None:
        """ADR-071 D4's additivity, as an executable claim: the key is absent
        and the exit is clean for a run that never opted in -- on the exact
        input that exits 1 *with* the setting."""
        run = _compare(
            str(bundle["v1"]), str(bundle["v1"]), bundle=bundle, require=False
        )
        assert run.returncode == 0, run.stdout + run.stderr
        doc = json.loads(run.stdout)
        assert "analysis_assurance" not in doc
        assert doc["exit"]["code"] == 0
        assert doc["exit"]["analysis_assurance_contribution"] == 0

    def test_a_non_json_format_still_explains_the_floor_on_stderr(
        self, bundle: dict[str, Path]
    ) -> None:
        """ADR-071 D6: a Markdown consumer with no JSON block must still
        learn why the run was floored, and which members fell short."""
        run = _compare(
            str(bundle["v1"]), str(bundle["v1_nodebug"]), bundle=bundle, fmt="markdown"
        )
        assert run.returncode == 1
        assert "Analysis assurance incomplete" in run.stderr
        assert "assurance.require_complete" in run.stderr
        for lib in _LIBS:
            assert f"libtoolkit_{lib}.so.1" in run.stderr

    def test_the_output_dir_sidecar_agrees_with_the_real_exit_code(
        self, bundle: dict[str, Path], tmp_path: Path
    ) -> None:
        """`--output-dir`'s `summary.json` builds its own `exit` block through
        the same resolver, so it must carry this axis too.

        Found by reading the wiring, not by a failing gate: the sidecar had
        every other orthogonal axis threaded into it and not this one, which
        would publish a `summary.json` claiming `exit.code: 0` for a run whose
        process status was `1`. That is precisely the self-contradicting report
        ADR-071 D5 exists to prevent, and the reason this asserts agreement
        between the sidecar and the process rather than just the presence of a
        field.
        """
        out = tmp_path / "sidecar"
        run = _compare(
            str(bundle["v1"]),
            str(bundle["v1_nodebug"]),
            "--output-dir",
            str(out),
            bundle=bundle,
        )
        assert run.returncode == 1, run.stdout + run.stderr
        sidecar = json.loads((out / "summary.json").read_text())
        assert sidecar["exit"]["code"] == run.returncode
        assert sidecar["exit"]["analysis_assurance_contribution"] == 1
        assert "analysis_assurance" in sidecar["exit"]["reasons"]
        assert sidecar["analysis_assurance"]["exit_contribution"] == 1
        # And the primary report and the sidecar agree with each other.
        primary = json.loads(run.stdout)
        assert (
            sidecar["analysis_assurance"]["status"]
            == primary["analysis_assurance"]["status"]
        )
        assert sidecar["exit"]["code"] == primary["exit"]["code"]

    def test_the_notice_never_claims_a_floor_beside_a_real_break(
        self, bundle: dict[str, Path]
    ) -> None:
        """The stderr notice's wording must reflect the *real* exit code.

        A second defect found by reading rather than by a failing gate: the
        notice was formatted by each caller with the caller's own guess at the
        compatibility exit, and the stored-facts driver had no severity code to
        guess from, so it passed `0` and would have announced "Exit code
        floored to 1" on a run that actually exited `4` on a real ABI break.
        `_exit_compare_release` now formats it from the decision it just
        resolved, which is the only place that number is known.

        Here `core` breaks (exit 4) while `thread` is short of evidence (it is
        the one member built without ``-g``), so the notice must say the axis's
        contribution *stands below* the real exit, not that it floored
        anything.
        """
        run = _compare(
            str(bundle["v1"]),
            str(bundle["v2_one_nodebug"]),
            bundle=bundle,
            fmt="markdown",
        )
        assert run.returncode == 4, run.stdout + run.stderr
        assert "Analysis assurance incomplete" in run.stderr
        assert "floored to" not in run.stderr
        assert "which stands" in run.stderr

    def test_every_report_the_run_writes_agrees_with_the_real_exit(
        self, bundle: dict[str, Path], tmp_path: Path
    ) -> None:
        """The gate must survive every document this run publishes.

        Codex security review (P1) found the floor reaching only `exit.
        analysis_assurance_contribution` and `analysis_assurance.
        exit_contribution`, while `workflows.aggregate.gate._analysis_assurance_
        exit` and the composite Action's `gate_mode: deferred` path read the
        canonical **top-level** `analysis_assurance_exit_contribution` (report
        schema 2.40, the exact sibling of the coverage axis's own key) -- so
        `abicheck compare` exited 1 and aggregating its report exited 0. A
        sibling P2 found the same loss in each per-library `{library}.json`,
        which was written without `require_complete_analysis` at all.

        Asserted as one invariant over *every* document rather than per file,
        and through the reader those consumers actually use rather than a key
        presence check: a future writer added without the axis fails here.
        """
        from abicheck.workflows.aggregate.gate import _analysis_assurance_exit

        out = tmp_path / "every"
        run = _compare(
            str(bundle["v1"]),
            str(bundle["v1_nodebug"]),
            "--output-dir",
            str(out),
            bundle=bundle,
        )
        assert run.returncode == 1, run.stdout + run.stderr
        documents = {"<stdout>": json.loads(run.stdout)}
        for path in sorted(out.glob("*.json")):
            documents[path.name] = json.loads(path.read_text())
        # The primary report, summary.json, and one file per member.
        assert len(documents) >= 2 + len(_LIBS)
        for name, doc in documents.items():
            assert _analysis_assurance_exit(doc) == 1, (
                f"{name}: the axis that gated this run reads "
                f"{_analysis_assurance_exit(doc)} to aggregate/the deferred gate"
            )
            assert doc["exit"]["code"] == run.returncode, name
            assert doc["exit"]["analysis_assurance_contribution"] == 1, name

    def test_a_clean_run_publishes_a_clean_axis_everywhere(
        self, bundle: dict[str, Path], tmp_path: Path
    ) -> None:
        """Negative control for the invariant above: with every member's
        analysis complete, the same reader must see `0` in every document --
        so the test above is not satisfied by a writer that hard-codes `1`."""
        from abicheck.workflows.aggregate.gate import _analysis_assurance_exit

        out = tmp_path / "clean"
        run = _compare(
            str(bundle["v1"]),
            str(bundle["v2"]),
            "--output-dir",
            str(out),
            headers_for=_LIBS,
            bundle=bundle,
        )
        assert run.returncode == 4, run.stdout + run.stderr
        docs = [json.loads(run.stdout)] + [
            json.loads(p.read_text()) for p in sorted(out.glob("*.json"))
        ]
        for doc in docs:
            assert _analysis_assurance_exit(doc) == 0

    def test_the_effective_config_receipt_names_the_gate_that_ran(
        self, bundle: dict[str, Path], tmp_path: Path
    ) -> None:
        """The receipt must describe the gate that produced the report.

        Codex review (P2): `_release_summary_effective_config_block` built its
        `EffectiveGate` without `require_complete_analysis`, so both the primary
        release JSON and `summary.json` published
        `effective_config_fields["gate.require_complete_analysis"] == "False"`
        -- and a digest indistinguishable from an ungated run -- for a run this
        axis actually gated. Asserted together with the digest *differing*
        between a gated and an ungated run on the same inputs, since the field
        alone could be right while the digest still collided.
        """
        out = tmp_path / "receipt"
        gated = _compare(
            str(bundle["v1"]),
            str(bundle["v1_nodebug"]),
            "--output-dir",
            str(out),
            bundle=bundle,
        )
        assert gated.returncode == 1, gated.stdout + gated.stderr
        ungated = _compare(
            str(bundle["v1"]), str(bundle["v1_nodebug"]), bundle=bundle, require=False
        )
        assert ungated.returncode == 0, ungated.stdout + ungated.stderr

        gated_docs = [
            json.loads(gated.stdout),
            json.loads((out / "summary.json").read_text()),
        ]
        for doc in gated_docs:
            assert (
                doc["effective_config_fields"]["gate.require_complete_analysis"]
                == "True"
            )
        ungated_doc = json.loads(ungated.stdout)
        assert (
            ungated_doc["effective_config_fields"]["gate.require_complete_analysis"]
            == "False"
        )
        # The digest has to move with the setting, or a consumer comparing
        # receipts cannot tell a gated run from an ungated one.
        assert (
            gated_docs[0]["effective_config_digest"]
            != ungated_doc["effective_config_digest"]
        )

    def test_the_stored_bundle_facts_driver_publishes_the_gate_too(
        self, bundle: dict[str, Path], tmp_path: Path
    ) -> None:
        """ADR-071 D8/D9 on the *other* driver, end to end.

        `compare_bundle_facts.dispatch` is a separate renderer and a separate
        set of writes from the live release fan-out, and Codex (P1) found it
        resolving the fold only *after* `_render` and every per-library write --
        so it exited 1 while its own report and each `--output-dir` file read 0
        to `aggregate`/the Action's deferred gate. Same invariant as
        `test_every_report_the_run_writes_agrees_with_the_real_exit`, asserted
        on this driver because sharing the fold function does not mean sharing
        the publishing path.
        """
        from abicheck.workflows.aggregate.gate import _analysis_assurance_exit

        facts = tmp_path / "old.bundlefacts.json"
        captured = _compare(
            str(bundle["v1"]),
            str(bundle["v1"]),
            "--bundle-facts-out",
            str(facts),
            bundle=bundle,
            require=False,
        )
        assert facts.exists(), captured.stdout + captured.stderr

        out = tmp_path / "stored_od"
        run = _compare(
            str(facts),
            str(bundle["v1_nodebug"]),
            "--output-dir",
            str(out),
            bundle=bundle,
        )
        assert run.returncode == 1, run.stdout + run.stderr
        primary = json.loads(run.stdout)
        assert _analysis_assurance_exit(primary) == 1
        assert primary["analysis_assurance"]["status"] != "complete"
        written = sorted(out.glob("*.json"))
        assert written, "the stored driver wrote no per-library reports"
        for path in written:
            doc = json.loads(path.read_text())
            assert _analysis_assurance_exit(doc) == 1, path.name

    def test_the_stored_driver_is_clean_when_nothing_fell_short(
        self, bundle: dict[str, Path], tmp_path: Path
    ) -> None:
        """Negative control for the stored driver: comparing a stored capture
        against the very tree it was captured from leaves nothing short, so the
        same reader must see `0` everywhere."""
        from abicheck.workflows.aggregate.gate import _analysis_assurance_exit

        facts = tmp_path / "same.bundlefacts.json"
        _compare(
            str(bundle["v1"]),
            str(bundle["v1"]),
            "--bundle-facts-out",
            str(facts),
            bundle=bundle,
            require=False,
        )
        assert facts.exists()
        run = _compare(str(facts), str(bundle["v1"]), bundle=bundle)
        doc = json.loads(run.stdout)
        assert _analysis_assurance_exit(doc) == 0
        assert run.returncode == 0, run.stdout + run.stderr
