# SPDX-License-Identifier: Apache-2.0
"""``compare-release``'s declared-deployment-floor digest projection --

split out of ``test_compare_release.py`` (that file sits at its own
``architecture/debt.yaml`` ``no_growth`` baseline with no headroom, the
same reason ``test_compare_release_annotations.py`` was split out).

Codex review, P2 (PR #1221 follow-up): a directory/package release with a
declared ``deployment:`` contract (``.abicheck.yml``'s ``deployment:``
block / ``EnvironmentMatrix``) must publish the same
``env_matrix_source_sha256`` content digest the scalar ``compare`` report
and the ``--no-baseline`` audit report already carry -- both per library
(surviving ``_strip_diff_results_and_adjust_verdict`` discarding each
library's ``DiffResult``) and once, at the release envelope, since one
release run threads the identical ``EnvironmentMatrix`` to every library.
Before the fix, neither was present: a within-floor release was
indistinguishable, in the rendered JSON, from one with no deployment
contract at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers (mirrors test_compare_release.py's own) ────────────────────────


def _snap(
    version: str = "1.0",
    funcs: list[Function] | None = None,
    library: str = "libfoo.so",
) -> AbiSnapshot:
    if funcs is None:
        funcs = [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ]
    return AbiSnapshot(
        library=library, version=version, functions=funcs, from_headers=True
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestReleaseJsonEnvMatrixDigest:
    def test_carries_the_digest_per_library_and_at_the_envelope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json"):
            snap = _snap()
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)
        (work / ".abicheck.yml").write_text(
            'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
        )
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 0, out
        data = json.loads(out)

        digest = data["env_matrix_source_sha256"]
        assert isinstance(digest, str) and digest.startswith("sha256:")
        assert len(data["libraries"]) == 2
        for lib in data["libraries"]:
            assert lib["env_matrix_source_sha256"] == digest, (
                f"{lib['library']}: per-library digest lost or diverged from "
                "the release-wide contract"
            )

    def test_omits_the_digest_with_no_deployment_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The converse: no ``deployment:`` contract means no digest
        anywhere -- never a fabricated one, and never present on one
        library but not another."""
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 0, out
        data = json.loads(out)

        assert "env_matrix_source_sha256" not in data
        for lib in data["libraries"]:
            assert "env_matrix_source_sha256" not in lib


class TestReleaseJsonEnvMatrixDigestWithNoCompletedComparison:
    """Codex review, P2 follow-up: the envelope digest must not be inferred
    from a per-library entry -- a release with a real declared
    ``deployment:`` contract but zero matched/completed library pairs
    previously carried *no* entry to read the digest off of at all, making
    a genuinely-configured contract indistinguishable from none. It is now
    computed directly from the resolved ``EnvironmentMatrix`` at release
    scope, before any per-library compare even runs."""

    def test_carries_the_digest_with_zero_matched_pairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        # Deliberately disjoint library names -- OLD and NEW share no
        # matched pair, so `library_results` (and therefore `libraries`)
        # is empty, while OLD/NEW discovery itself still succeeds.
        _write_snap(old_dir / "libfoo.json", _snap(library="libfoo.so"))
        _write_snap(new_dir / "libbar.json", _snap(library="libbar.so"))
        (work / ".abicheck.yml").write_text(
            'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
        )
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        data = json.loads(out)

        assert data["libraries"] == [], "fixture precondition: zero matched pairs"
        digest = data["env_matrix_source_sha256"]
        assert isinstance(digest, str) and digest.startswith("sha256:")
        assert data["effective_config_fields"]["policy.env_matrix"] == digest

    def test_matches_the_digest_from_a_run_with_a_completed_pair(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The digest is a pure function of the resolved matrix, not of how
        many comparisons happened to complete -- so a zero-pair run and a
        normal run under the identical ``deployment:`` config must agree."""
        deployment_yaml = 'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'

        zero_pair_work = tmp_path / "zero_pair"
        zero_pair_work.mkdir()
        zp_old = zero_pair_work / "old"
        zp_old.mkdir()
        zp_new = zero_pair_work / "new"
        zp_new.mkdir()
        _write_snap(zp_old / "libfoo.json", _snap(library="libfoo.so"))
        _write_snap(zp_new / "libbar.json", _snap(library="libbar.so"))
        (zero_pair_work / ".abicheck.yml").write_text(deployment_yaml)
        monkeypatch.chdir(zero_pair_work)
        _, zero_pair_out = _invoke("compare", str(zp_old), str(zp_new), "-o", "json=-")
        zero_pair_digest = json.loads(zero_pair_out)["env_matrix_source_sha256"]

        matched_work = tmp_path / "matched"
        matched_work.mkdir()
        m_old = matched_work / "old"
        m_old.mkdir()
        m_new = matched_work / "new"
        m_new.mkdir()
        snap = _snap()
        _write_snap(m_old / "libfoo.json", snap)
        _write_snap(m_new / "libfoo.json", snap)
        (matched_work / ".abicheck.yml").write_text(deployment_yaml)
        monkeypatch.chdir(matched_work)
        matched_code, matched_out = _invoke(
            "compare", str(m_old), str(m_new), "-o", "json=-"
        )
        assert matched_code == 0, matched_out
        matched_digest = json.loads(matched_out)["env_matrix_source_sha256"]

        assert zero_pair_digest == matched_digest

    def test_output_dir_summary_also_carries_the_digest_with_zero_matched_pairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ``--output-dir`` sidecar (``summary.json``) shares the same
        gap this fix closes for the primary JSON report -- it must carry
        the digest too, and for the same zero-matched-pairs case."""
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap(library="libfoo.so"))
        _write_snap(new_dir / "libbar.json", _snap(library="libbar.so"))
        (work / ".abicheck.yml").write_text(
            'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
        )
        monkeypatch.chdir(work)
        output_dir = work / "out"
        output_dir.mkdir()

        _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "json=-",
            "-o",
            f"json={output_dir}/",
        )
        summary = json.loads((output_dir / "summary.json").read_text())
        digest = summary["env_matrix_source_sha256"]
        assert isinstance(digest, str) and digest.startswith("sha256:")
        assert summary["effective_config_fields"]["policy.env_matrix"] == digest


class TestReleaseMarkdownEnvMatrixDigest:
    """Codex review, P2 follow-up (round-8): the release Markdown report
    never exposed the digest at all -- unlike JSON's envelope field, there
    was no projection point for it whatsoever, even when comparisons
    completed normally."""

    def test_carries_the_digest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json"):
            snap = _snap()
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)
        (work / ".abicheck.yml").write_text(
            'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
        )
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "markdown=-")
        assert code == 0, out
        assert "Deployment floor digest:" in out
        assert "`sha256:" in out

    def test_omits_the_digest_with_no_deployment_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "markdown=-")
        assert code == 0, out
        assert "Deployment floor digest:" not in out


class TestReleaseJunitEnvMatrixDigest:
    """Codex review, P2 follow-up (round-8): the release JUnit report only
    exposed the digest indirectly through completed per-library
    ``<testsuite>``s (via ``_add_env_matrix_property``) -- so a release with
    zero matched/completed pairs lost the digest entirely, since no
    per-library testsuite existed to carry it. Fixed as a dedicated
    release-level ``<testsuite name="abicheck.deployment">`` property that
    does not depend on any library comparison having completed."""

    def test_carries_the_digest_as_a_release_level_property(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json"):
            snap = _snap()
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)
        (work / ".abicheck.yml").write_text(
            'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
        )
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "junit=-")
        assert code == 0, out
        assert 'name="abicheck.deployment"' in out
        assert 'name="env_matrix_source_sha256"' in out
        # The dedicated deployment testsuite must not perturb pass/fail
        # counts a CI dashboard reads (it's a zero-test/zero-error suite).
        import xml.etree.ElementTree as ET

        root = ET.fromstring(out)
        deployment_suite = next(
            ts
            for ts in root.findall("testsuite")
            if ts.get("name") == "abicheck.deployment"
        )
        assert deployment_suite.get("tests") == "0"
        assert deployment_suite.get("errors") == "0"
        assert deployment_suite.get("failures") == "0"

    def test_carries_the_digest_with_zero_matched_pairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The specific case this fix targets: no per-library `<testsuite>`
        exists at all (disjoint library names, so zero matched pairs), yet
        the release-level `abicheck.deployment` testsuite must still carry
        the digest -- this is exactly what per-library-only exposure could
        never do."""
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap(library="libfoo.so"))
        _write_snap(new_dir / "libbar.json", _snap(library="libbar.so"))
        (work / ".abicheck.yml").write_text(
            'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
        )
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "junit=-")
        # Zero matched pairs floors the exit code to 1 (ADR-065 D7's "no
        # comparison completed" completeness axis) -- unrelated to this fix,
        # and the same non-zero exit `TestReleaseJsonEnvMatrixDigestWithNo
        # CompletedComparison`'s JSON sibling test implicitly accepts too.
        assert code == 1, out
        assert 'name="abicheck.deployment"' in out
        assert 'name="env_matrix_source_sha256"' in out

        import xml.etree.ElementTree as ET

        # A non-zero exit also prints the scope's stderr notice around the
        # JUnit document on stdout (CliRunner mixes both streams) -- slice
        # to just the real XML document before parsing it.
        xml_start = out.index("<?xml")
        xml_end = out.index("</testsuites>") + len("</testsuites>")
        root = ET.fromstring(out[xml_start:xml_end])
        # No per-library testsuite carries the property -- confirming the
        # digest genuinely comes from the dedicated release-level suite,
        # not incidentally from a library comparison that happened anyway.
        library_suites = [
            ts
            for ts in root.findall("testsuite")
            if ts.get("name") != "abicheck.deployment"
        ]
        for ts in library_suites:
            props = ts.find("properties")
            names = (
                {p.get("name") for p in props.findall("property")}
                if props is not None
                else set()
            )
            assert "env_matrix_source_sha256" not in names

    def test_omits_the_suite_with_no_deployment_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "junit=-")
        assert code == 0, out
        assert 'name="abicheck.deployment"' not in out
