"""Versioning-policy and consumer-impact sections across every report view.

Two contracts, both stated through the public ``compare`` CLI:

* The project's versioning policy decides *acceptance* only. Every view
  (JSON, Markdown, review digest, HTML) states the same acceptance, and
  adding or changing the policy never moves the verdict, the finding set or
  the exit code.
* ``--use-cases`` attribution rendered as HTML lists exactly the findings
  the JSON block attributes, and a report without the flag has no section.

The acceptance oracle below is written from the user documentation's rule
table, deliberately not by calling ``evaluate_release_acceptance``.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.model import BuildSourceManifest
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

PROMISES = ("none", "abi_within_major", "api_within_minor", "source_within_minor")
ENFORCEMENTS = ("warn", "block")
#: name -> (old functions, new functions, expected verdict)
PAIRS = {
    "no_change": (["a", "b"], ["a", "b"], "NO_CHANGE"),
    "addition": (["a"], ["a", "b"], "COMPATIBLE"),
    "removal": (["a", "b"], ["a"], "BREAKING"),
}


def _expected_accepted(verdict: str, promise: str, enforcement: str) -> bool:
    """Independent oracle from the documented rule."""
    if verdict in ("NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"):
        return True
    if promise == "none":
        return True
    deviates = verdict == "BREAKING" or promise != "abi_within_major"
    return not deviates or enforcement == "warn"


def test_oracle_is_not_constant() -> None:
    values = {
        _expected_accepted(v, p, e)
        for v in ("BREAKING", "API_BREAK", "COMPATIBLE")
        for p in PROMISES
        for e in ENFORCEMENTS
    }
    assert values == {True, False}


def _graph(names: list[str]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for name in names:
        g.add_node(
            GraphNode(
                id=f"decl://{name}",
                kind="source_decl",
                label=name,
                attrs={"visibility": "public_header"},
            )
        )
        g.add_node(
            GraphNode(id=f"binary_symbol://{name}", kind="binary_symbol", label=name)
        )
        g.add_edge(
            GraphEdge(
                src=f"decl://{name}",
                dst=f"binary_symbol://{name}",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            )
        )
    return g


def _write(
    tmp: Path, name: str, version: str, funcs: list[str], graph: bool = False
) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(name=f, mangled=f, return_type="int", visibility=Visibility.PUBLIC)
            for f in funcs
        ],
    )
    if graph:
        snap.build_source = BuildSourcePack(
            root=Path("."), manifest=BuildSourceManifest(), source_graph=_graph(funcs)
        )
    path = tmp / f"{name}.json"
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _run(args: list[str]) -> int:
    result = CliRunner().invoke(main, args)
    assert result.exit_code in (0, 1, 2, 4), result.output
    return result.exit_code


def _render_all(
    tmp: Path, old: Path, new: Path, extra: list[str]
) -> tuple[int, dict[str, str]]:
    outs = {fmt: tmp / f"r.{fmt}" for fmt in ("json", "markdown", "review", "html")}
    args = ["compare", str(old), str(new), *extra]
    for fmt, path in outs.items():
        args += ["-o", f"{fmt}={path}"]
    code = _run(args)
    return code, {fmt: path.read_text(encoding="utf-8") for fmt, path in outs.items()}


class TestVersioningPolicyAcrossViews:
    @pytest.mark.parametrize(
        ("pair", "promise", "enforcement"),
        list(itertools.product(PAIRS, PROMISES, ENFORCEMENTS)),
    )
    def test_every_view_states_the_same_acceptance(
        self, tmp_path: Path, pair: str, promise: str, enforcement: str
    ) -> None:
        old_funcs, new_funcs, verdict = PAIRS[pair]
        old = _write(tmp_path, "old", "1.0", old_funcs)
        new = _write(tmp_path, "new", "1.1", new_funcs)
        policy = tmp_path / "policy.yaml"
        policy.write_text(
            f"versioning:\n  promise: {promise}\n  enforcement: {enforcement}\n",
            encoding="utf-8",
        )
        code, out = _render_all(tmp_path, old, new, ["--policy", str(policy)])
        base_code, base = _render_all(tmp_path, old, new, [])

        report = json.loads(out["json"])
        assert report["verdict"] == verdict
        acceptance = report["release_recommendation"]["policy_acceptance"]
        expected = _expected_accepted(verdict, promise, enforcement)
        assert acceptance["accepted"] is expected
        assert acceptance["promise"] == promise
        assert acceptance["enforcement"] == enforcement

        label = (
            "Versioning policy: Accepted"
            if expected
            else "Versioning policy: Not accepted"
        )
        assert label in out["html"]
        state = "accepted" if expected else "not accepted"
        assert f"| Versioning policy | {state} (promise `{promise}`" in out["markdown"]
        assert f"**Versioning policy:** {state} (promise `{promise}`" in out["review"]

        # Policy decides acceptance, never facts.
        base_report = json.loads(base["json"])
        assert code == base_code
        assert base_report["verdict"] == report["verdict"]
        assert [c["kind"] for c in base_report["changes"]] == [
            c["kind"] for c in report["changes"]
        ]
        rec, base_rec = (
            report["release_recommendation"],
            base_report["release_recommendation"],
        )
        assert {k: v for k, v in rec.items() if k != "policy_acceptance"} == {
            k: v for k, v in base_rec.items() if k != "policy_acceptance"
        }

    def test_no_stated_policy_renders_no_section(self, tmp_path: Path) -> None:
        old = _write(tmp_path, "old", "1.0", ["a", "b"])
        new = _write(tmp_path, "new", "2.0", ["a"])
        _, out = _render_all(tmp_path, old, new, [])
        assert (
            json.loads(out["json"])["release_recommendation"]["policy_acceptance"]
            is None
        )
        assert "id='versioning-policy'" not in out["html"]
        assert "Versioning policy" not in out["markdown"]
        assert "Versioning policy" not in out["review"]

    @pytest.mark.parametrize(
        "verdict",
        ["NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK", "BREAKING"],
    )
    @pytest.mark.parametrize(
        ("promise", "enforcement"), list(itertools.product(PROMISES, ENFORCEMENTS))
    )
    def test_html_compute_covers_every_verdict(
        self, tmp_path: Path, verdict: str, promise: str, enforcement: str
    ) -> None:
        # API_BREAK and the risk tier have no cheap CLI fixture; the compute
        # half reads only the verdict, so a real DiffResult is enough.
        from abicheck.checker_types import DiffResult
        from abicheck.html_report import compute_versioning_policy
        from abicheck.policy.classification import Verdict
        from abicheck.policy_file import PolicyFile

        path = tmp_path / "policy.yaml"
        path.write_text(
            f"versioning:\n  promise: {promise}\n  enforcement: {enforcement}\n",
            encoding="utf-8",
        )
        policy_file = PolicyFile.load(path)
        result = DiffResult(
            old_version="1",
            new_version="2",
            library="libfoo.so",
            verdict=Verdict(verdict),
            policy_file=policy_file,
        )
        acceptance = compute_versioning_policy(result)
        assert acceptance is not None
        assert acceptance["accepted"] is _expected_accepted(
            verdict, promise, enforcement
        )


class TestUseCaseImpactHtml:
    MANIFEST = "- use_case: training\n  entrypoints: [b]\n- use_case: serving\n  entrypoints: [a]\n"

    def test_html_lists_exactly_the_json_attribution(self, tmp_path: Path) -> None:
        old = _write(tmp_path, "old", "1", ["a", "b"], graph=True)
        new = _write(tmp_path, "new", "2", ["a"], graph=True)
        manifest = tmp_path / "uc.yaml"
        manifest.write_text(self.MANIFEST, encoding="utf-8")
        _, out = _render_all(tmp_path, old, new, ["--use-cases", str(manifest)])

        block = json.loads(out["json"])["use_case_impact"]
        html_out = out["html"]
        assert "id='use-case-impact'" in html_out
        assert block["by_use_case"], block  # the fixture must attribute something
        for entry in block["use_cases"]:
            name = entry["use_case"]
            changes = block["by_use_case"].get(name, [])
            row_start = html_out.index(
                f"<tr><td><code>{name}</code></td><td>{len(changes)}</td>"
            )
            row = html_out[row_start : html_out.index("</tr>", row_start)]
            for change in changes:
                assert f"<code>{change['symbol']}</code>" in row
            assert ("<span class='cat-badge'>affected</span>" in row) is bool(changes)
        assert (
            f"{block['unattributed_changes']} of {block['total_changes']} change(s)"
            in html_out
        )

    def test_no_flag_no_section(self, tmp_path: Path) -> None:
        old = _write(tmp_path, "old", "1", ["a", "b"], graph=True)
        new = _write(tmp_path, "new", "2", ["a"], graph=True)
        _, out = _render_all(tmp_path, old, new, [])
        assert "id='use-case-impact'" not in out["html"]
        assert "use_case_impact" not in json.loads(out["json"])
