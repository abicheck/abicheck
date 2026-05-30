# Copyright 2026 Nikolay Petrov
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

"""CLI tests for the ``probe`` command group (``cli_probe.py``).

The matrix harness is exercised at the snapshot level (synthetic
``MatrixSnapshot`` JSON) so these tests need no compiler — compilation
itself is covered by ``test_probe_harness.py``. ``probe run`` is driven
with ``run_probe_matrix`` monkeypatched for the same reason.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from defusedxml.ElementTree import fromstring as xml_fromstring

from abicheck.cli import main


def _write_matrix(
    path: Path,
    *,
    version: str,
    cxx_stds: dict[str, int | None],
    defaults: dict[str, str],
) -> None:
    path.write_text(
        json.dumps(
            {
                "library": "onedpl",
                "version": version,
                "spec_name": "onedpl",
                "cxx_stds": cxx_stds,
                "defaults": defaults,
                "results": [],
            }
        )
    )


def _write_failed_matrix(path: Path, *, version: str) -> None:
    """A matrix whose only probe result failed to compile (no snapshot)."""
    path.write_text(
        json.dumps(
            {
                "library": "onedpl",
                "version": version,
                "spec_name": "onedpl",
                "cxx_stds": {"gcc_cxx20": 20},
                "defaults": {"backend": "tbb"},
                "results": [
                    {
                        "configuration_id": "gcc_cxx20",
                        "probe_id": "sort",
                        "object_path": None,
                        "snapshot": None,
                        "error": "compiler 'g++' not found on PATH",
                    }
                ],
            }
        )
    )


# ---------------------------------------------------------------------------
# probe compare
# ---------------------------------------------------------------------------


class TestProbeCompare:
    def _matrices(self, tmp_path: Path) -> tuple[Path, Path]:
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        _write_matrix(
            old,
            version="1.0",
            cxx_stds={"a": 17, "b": 20},
            defaults={"backend": "tbb", "execution_policy": "seq"},
        )
        _write_matrix(
            new,
            version="2.0",
            cxx_stds={"b": 20, "c": 23},
            defaults={"backend": "tbb", "execution_policy": "par"},
        )
        return old, new

    def test_json_reports_both_findings(self, tmp_path: Path) -> None:
        old, new = self._matrices(tmp_path)
        result = CliRunner().invoke(main, ["probe", "compare", str(old), str(new)])
        # API_BREAK (floor raised) → exit 2
        assert result.exit_code == 2, result.output
        payload = json.loads(result.output)
        kinds = {c["kind"] for c in payload["changes"]}
        assert "cxx_standard_floor_raised" in kinds
        assert "behavioural_default_changed" in kinds

    def test_markdown_format(self, tmp_path: Path) -> None:
        old, new = self._matrices(tmp_path)
        result = CliRunner().invoke(
            main, ["probe", "compare", str(old), str(new), "-f", "markdown"]
        )
        assert result.exit_code == 2
        assert "cxx_standard_floor_raised" in result.output

    def test_sarif_format_is_valid(self, tmp_path: Path) -> None:
        old, new = self._matrices(tmp_path)
        result = CliRunner().invoke(
            main, ["probe", "compare", str(old), str(new), "-f", "sarif"]
        )
        assert result.exit_code == 2
        doc = json.loads(result.output)
        assert len(doc["runs"][0]["results"]) == 2

    def test_junit_format_is_valid_xml(self, tmp_path: Path) -> None:
        old, new = self._matrices(tmp_path)
        result = CliRunner().invoke(
            main, ["probe", "compare", str(old), str(new), "-f", "junit"]
        )
        assert result.exit_code == 2
        xml_fromstring(result.output)  # raises on malformed XML

    def test_output_to_file(self, tmp_path: Path) -> None:
        old, new = self._matrices(tmp_path)
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main, ["probe", "compare", str(old), str(new), "-o", str(out)]
        )
        assert result.exit_code == 2
        assert json.loads(out.read_text())["changes"]

    def test_no_changes_exit_zero(self, tmp_path: Path) -> None:
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        same = {"backend": "tbb"}
        _write_matrix(old, version="1.0", cxx_stds={"a": 20}, defaults=same)
        _write_matrix(new, version="2.0", cxx_stds={"a": 20}, defaults=same)
        result = CliRunner().invoke(main, ["probe", "compare", str(old), str(new)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["verdict"] in ("NO_CHANGE", "COMPATIBLE")


class TestProbeCompareIncompleteMatrix:
    """An all-failed matrix (e.g. missing compiler) must not diff clean."""

    def test_failed_results_rejected_with_exit_3(self, tmp_path: Path) -> None:
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        _write_failed_matrix(old, version="1.0")
        _write_failed_matrix(new, version="2.0")
        result = CliRunner().invoke(main, ["probe", "compare", str(old), str(new)])
        assert result.exit_code == 3, result.output
        assert "Refusing to diff an incomplete matrix" in result.output
        assert "not found on PATH" in result.output

    def test_failure_in_only_one_matrix_still_rejected(self, tmp_path: Path) -> None:
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        _write_matrix(
            old, version="1.0", cxx_stds={"a": 20}, defaults={"backend": "tbb"}
        )
        _write_failed_matrix(new, version="2.0")
        result = CliRunner().invoke(main, ["probe", "compare", str(old), str(new)])
        assert result.exit_code == 3, result.output

    def test_allow_failures_diffs_and_marks_low_confidence(
        self, tmp_path: Path
    ) -> None:
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        # Defaults differ → a real finding survives, but the matrix is partial.
        _write_failed_matrix(old, version="1.0")
        new.write_text(
            json.dumps(
                {
                    "library": "onedpl",
                    "version": "2.0",
                    "spec_name": "onedpl",
                    "cxx_stds": {"gcc_cxx20": 20},
                    "defaults": {"backend": "openmp"},
                    "results": [
                        {
                            "configuration_id": "gcc_cxx20",
                            "probe_id": "sort",
                            "object_path": None,
                            "snapshot": None,
                            "error": "compiler 'g++' not found on PATH",
                        }
                    ],
                }
            )
        )
        result = CliRunner().invoke(
            main, ["probe", "compare", str(old), str(new), "--allow-failures"]
        )
        # behavioural_default_changed is risk → verdict not API_BREAK → exit 0.
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["confidence"] == "low"
        assert any(
            c["kind"] == "behavioural_default_changed" for c in payload["changes"]
        )

    def test_clean_matrix_unaffected(self, tmp_path: Path) -> None:
        # A matrix with no failed results is diffed normally (high confidence).
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        _write_matrix(
            old, version="1.0", cxx_stds={"a": 20}, defaults={"backend": "tbb"}
        )
        _write_matrix(
            new, version="2.0", cxx_stds={"a": 20}, defaults={"backend": "tbb"}
        )
        result = CliRunner().invoke(main, ["probe", "compare", str(old), str(new)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["confidence"] == "high"


# ---------------------------------------------------------------------------
# probe run
# ---------------------------------------------------------------------------


class TestProbeRun:
    def _spec(self, tmp_path: Path) -> Path:
        spec = tmp_path / "spec.yaml"
        spec.write_text(
            "name: onedpl\n"
            "configurations:\n"
            "  - id: gcc_cxx20\n"
            "    compiler: g++\n"
            "    flags: [-std=c++20]\n"
            "probes:\n"
            "  - name: sort\n"
            "    headers: [vector]\n"
            "    body: |\n"
            "      void probe() {}\n"
            "defaults:\n"
            "  backend: tbb\n"
        )
        return spec

    def test_run_writes_matrix(self, tmp_path: Path, monkeypatch) -> None:
        from abicheck import cli_probe
        from abicheck.probe_harness import MatrixSnapshot, ProbeResult

        def fake_run(spec, *, library_name, version, work_dir, snapshot):
            return MatrixSnapshot(
                library=library_name,
                version=version,
                spec_name=spec.name,
                cxx_stds={"gcc_cxx20": 20},
                defaults=dict(spec.defaults),
                results=[ProbeResult("gcc_cxx20", "sort", error=None)],
            )

        monkeypatch.setattr(cli_probe, "run_probe_matrix", fake_run)
        out = tmp_path / "m.json"
        result = CliRunner().invoke(
            main,
            [
                "probe",
                "run",
                str(self._spec(tmp_path)),
                "--library",
                "onedpl",
                "--version",
                "2022.0",
                "--out",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text())
        assert data["library"] == "onedpl"
        assert data["version"] == "2022.0"

    def test_run_reports_failures_on_stderr(self, tmp_path: Path, monkeypatch) -> None:
        from abicheck import cli_probe
        from abicheck.probe_harness import MatrixSnapshot, ProbeResult

        def fake_run(spec, *, library_name, version, work_dir, snapshot):
            return MatrixSnapshot(
                library=library_name,
                version=version,
                spec_name=spec.name,
                results=[
                    ProbeResult("c", "p", error="compiler 'g++' not found on PATH")
                ],
            )

        monkeypatch.setattr(cli_probe, "run_probe_matrix", fake_run)
        # No --out → matrix JSON on stdout, run summary + per-failure
        # lines on stderr. The summary names the failure count and the
        # offending configuration/probe so it is actionable.
        result = CliRunner().invoke(
            main,
            [
                "probe",
                "run",
                str(self._spec(tmp_path)),
                "--library",
                "onedpl",
                "--version",
                "1.0",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "1 failure(s)" in result.output
        assert "not found on PATH" in result.output


# ---------------------------------------------------------------------------
# Shipped oneDPL manifest parses
# ---------------------------------------------------------------------------


def test_onedpl_example_spec_parses() -> None:
    from abicheck.probe_harness import load_probe_spec

    spec_path = (
        Path(__file__).resolve().parent.parent / "examples" / "probes" / "onedpl.yaml"
    )
    spec = load_probe_spec(spec_path)
    assert spec.name == "onedpl"
    assert len(spec.configurations) == 3
    assert len(spec.probes) == 2
    assert spec.defaults["execution_policy"] == "par"
    # -std=c++NN parsing populated the floor for each configuration.
    assert {c.cxx_std for c in spec.configurations} == {17, 20}
