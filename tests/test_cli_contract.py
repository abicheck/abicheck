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

"""CLI interface-contract gate mirror + chokepoint parity (ADR-037 / G22 Phase 1).

This is the unit-test mirror of the ``cli-contract`` AI-readiness check
(``scripts/check_ai_readiness.py``), so the contract is enforced both as a fast
CI gate and in the regular test suite. It also pins the *behavioural* payoff of
the single chokepoint: ``compare-release`` and ``service.run_compare`` classify
a given pair identically (no ``scope_public`` default drift).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the gate from scripts/ — the AI-readiness module is pure stdlib.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402
from abicheck.serialization import save_snapshot  # noqa: E402
from scripts.check_ai_readiness import Findings, check_cli_contract  # noqa: E402

# ── D10.1: no front-end skips the Tier-2 service ─────────────────────────────


def test_no_tier_skip() -> None:
    """No ``abicheck/cli*.py`` module calls Tier-1 ``checker.compare`` directly.

    Front-ends must route through ``service.run_compare`` /
    ``service.compare_snapshots`` (ADR-037 D1/D10.1).
    """
    findings = Findings()
    check_cli_contract(findings)
    contract_errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert contract_errors == [], "Tier-1 call sites in front-ends:\n" + "\n".join(
        contract_errors
    )


# Each case plants one front-end module that reaches Tier-1 `checker.compare`
# a different way; the gate must flag exactly one violation naming that file.
# (filename, source) — covers: direct import, aliased lazy `compare` import,
# aliased `checker` *module* call, and the non-`cli*.py` `appcompat.py` scope.
_GATE_VIOLATION_CASES: list[pytest.ParameterSet] = [
    pytest.param(
        "cli_bad.py",
        "from .checker import compare\ndef go(a, b):\n    return compare(a, b)\n",
        id="direct-import",
    ),
    pytest.param(
        "cli_alias.py",
        "def go(a, b):\n"
        "    from .checker import compare as _compare\n"
        "    return _compare(a, b)\n",
        id="aliased-lazy-import",
    ),
    pytest.param(
        "cli_modalias.py",
        "from . import checker as core\ndef go(a, b):\n    return core.compare(a, b)\n",
        id="aliased-module-call",
    ),
    pytest.param(
        "appcompat.py",
        "from .checker import compare\ndef check(a, b):\n    return compare(a, b)\n",
        id="appcompat-in-scope",
    ),
]


@pytest.mark.parametrize("filename, source", _GATE_VIOLATION_CASES)
def test_gate_flags_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
) -> None:
    """The gate is not a no-op: each way of reaching Tier-1 is caught once."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / filename).write_text(source)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert filename in errors[0]


def test_service_compare_call_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routing through ``service.compare_snapshots`` must NOT be flagged."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_ok.py").write_text(
        "from .service import compare_snapshots\n"
        "def go(a, b):\n"
        "    return compare_snapshots(a, b)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


# ── D10.2: shared-decorator coverage (ADR-037 D3 / G22 Phase 2) ──────────────


def _registered_commands() -> dict:
    """Force every verdict-emitting command module to register on ``main``."""
    import abicheck.cli_appcompat  # noqa: F401  — registers `appcompat`
    from abicheck.cli import main

    return main.commands


def _command_flags(cmd: object) -> set[str]:
    flags: set[str] = set()
    for p in cmd.params:  # type: ignore[attr-defined]
        if getattr(p, "param_type_name", None) != "option":
            continue
        flags.update(p.opts)
        flags.update(p.secondary_opts)
    return flags


def test_decorator_coverage() -> None:
    """Every verdict-emitting command carries each required shared option family
    (in full), or is on the ``INTENTIONAL_SUBSET`` allowlist (ADR-037 D10.2).

    This introspects the *live Click params* — stronger than the gate's AST
    decorator scan, so a family applied but secretly stripped would still fail.
    """
    from abicheck import cli_options as co

    commands = _registered_commands()
    for cmd_name in co.VERDICT_EMITTING_COMMANDS:
        flags = _command_flags(commands[cmd_name])
        for family in co.REQUIRED_FAMILIES:
            if (cmd_name, family) in co.INTENTIONAL_SUBSET:
                continue
            missing = co.FAMILY_FLAGS[family] - flags
            assert not missing, (
                f"{cmd_name} is missing {family} flags {sorted(missing)} — "
                "compose the shared decorator or add an INTENTIONAL_SUBSET entry"
            )


def test_intentional_subset_entries_are_real_gaps() -> None:
    """An allowlisted (command, family) must be a *genuine* omission — otherwise
    the allowlist rots into a rubber stamp for families that are actually present."""
    from abicheck import cli_options as co

    commands = _registered_commands()
    for (cmd_name, family), reason in co.INTENTIONAL_SUBSET.items():
        assert reason.strip(), f"{cmd_name}/{family} needs a non-empty reason"
        flags = _command_flags(commands[cmd_name])
        assert co.FAMILY_FLAGS[family] - flags, (
            f"{cmd_name} actually carries the whole {family} family — drop the "
            "INTENTIONAL_SUBSET entry"
        )


def test_gate_flags_missing_decorator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D10.2 is not a no-op: a verdict command lacking a required family is caught."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    # A `compare` command that composes only some of the required families.
    (pkg / "cli.py").write_text(
        "import click\n"
        '@main.command("compare")\n'
        "@two_sided_input_options\n"
        "@policy_options\n"
        "def compare_cmd():\n"
        "    pass\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    # severity/scope/output are missing → three coverage errors naming `compare`.
    missing = {fam for fam in ("severity_options", "scope_options", "output_options")
               if any(fam in m and "compare" in m for m in msgs)}
    assert missing == {"severity_options", "scope_options", "output_options"}, msgs


def test_gate_flags_missing_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mapped command whose module exists but no longer declares it is flagged
    (D10.2 must not silently pass when coverage can't be verified)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    # cli.py exists but the `compare` command has been removed from it.
    (pkg / "cli.py").write_text("def helper():\n    return 1\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert any("`compare` was not found" in m for m in msgs), msgs


def test_intentional_subset_decorator_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command listed in the intentional-subset allowlist may omit a required
    family without being flagged. The real allowlist is empty today, so this
    drives the mechanism with a synthetic command + allowlist entry."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_synth.py").write_text(
        "import click\n"
        '@main.command("synth")\n'
        "@two_sided_input_options\n"
        "@policy_options\n"
        "@scope_options\n"
        "@output_options(['json'])\n"
        "def synth_cmd():\n"
        "    pass\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_VERDICT_CMD_MODULES", {"cli_synth.py": "synth"})
    monkeypatch.setattr(
        gate, "_INTENTIONAL_SUBSET_DECORATORS", frozenset({("synth", "severity_options")})
    )

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert not any("synth" in m and "severity_options" in m for m in msgs), msgs


# ── D10.4: one default per flag (ADR-037 D3 / G22 Phase 2) ───────────────────


def test_one_default_per_flag() -> None:
    """The real ``cli_options.py`` has no un-deferred conflicting flag default."""
    import scripts.check_ai_readiness as gate

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    assert [m for c, m in findings.errors if c == "cli-contract"] == []


def test_gate_flags_conflicting_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D10.4 catches the same ``--flag`` declared with two different defaults."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_options.py").write_text(
        "import click\n"
        "def a(func):\n"
        '    return click.option("--mode", default="off")(func)\n'
        "def b(func):\n"
        '    return click.option("--mode", default="on")(func)\n'
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(msgs) == 1 and "--mode" in msgs[0], msgs


def test_conflicting_defaults_always_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the deprecation-era allowlist gone, any flag declared with two
    different defaults across shared decorators is flagged (ADR-037 D10.4)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_options.py").write_text(
        "import click\n"
        "def a(func):\n"
        '    return click.option("--collect-mode", default="off")(func)\n'
        "def b(func):\n"
        '    return click.option("--collect-mode", default="source-target")(func)\n'
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(msgs) == 1 and "--collect-mode" in msgs[0], msgs


# ── Gate tables mirror the cli_options source of truth ───────────────────────


def test_gate_tables_mirror_cli_options() -> None:
    """The pure-stdlib gate duplicates ``cli_options`` contract tables (it cannot
    import the package). Assert the two never drift (ADR-037 D10)."""
    import scripts.check_ai_readiness as gate
    from abicheck import cli_options as co

    # command ↔ module map (inverted between the two).
    assert gate._VERDICT_CMD_MODULES == {
        mod: cmd for cmd, mod in co.VERDICT_EMITTING_COMMANDS.items()
    }
    # required decorators = the decorator for each required family.
    assert gate._REQUIRED_FAMILY_DECORATORS == frozenset(
        co.FAMILY_DECORATOR[f] for f in co.REQUIRED_FAMILIES
    )
    # allowlist, mapped from (cmd, family) to (cmd, decorator).
    assert gate._INTENTIONAL_SUBSET_DECORATORS == frozenset(
        (cmd, co.FAMILY_DECORATOR[fam]) for (cmd, fam) in co.INTENTIONAL_SUBSET
    )


# ── D10.3: MCP ⇄ CLI name-map completeness (ADR-037 / G22 Phase 6) ───────────


def _has_mcp() -> bool:
    import importlib.util

    return importlib.util.find_spec("mcp") is not None


@pytest.mark.skipif(not _has_mcp(), reason="MCP dependencies not installed")
def test_mcp_cli_name_map_complete() -> None:
    """Every ``abi_compare`` MCP param has a row in ``MCP_CLI_NAME_MAP``.

    Live introspection of the actual tool signature (stronger than the gate's
    AST scan): a new MCP param that forgets its name-map row is caught here, so
    the MCP and CLI front-ends cannot silently diverge (ADR-037 D10.3).
    """
    import inspect

    import scripts.check_ai_readiness as gate
    from abicheck import cli_options as co, mcp_server

    sig = inspect.signature(mcp_server.abi_compare)
    params = set(sig.parameters)
    # Mirror the gate's exemption set (framework-plumbing params) so an
    # intentionally-exempt param does not fail here while the gate allows it.
    missing = params - set(co.MCP_CLI_NAME_MAP) - set(gate._MCP_NAME_MAP_EXEMPT_PARAMS)
    assert not missing, (
        f"abi_compare params absent from MCP_CLI_NAME_MAP: {sorted(missing)} — "
        "add a row mapping each to its compare flag (or None)."
    )


def test_mcp_cli_name_map_values_are_real_compare_flags() -> None:
    """Each non-``None`` map value names a real ``compare`` flag (or positional).

    Keeps the CLI side of the map honest: a typo'd or removed flag is caught
    (ADR-037 D10.3). Positional-operand rows are spelled with parentheses and
    are exempt from the flag-set check.
    """
    from abicheck import cli_options as co

    compare_flags = _command_flags(_registered_commands()["compare"])
    for mcp_param, cli_name in co.MCP_CLI_NAME_MAP.items():
        if cli_name is None or "(" in cli_name:
            continue  # no-flag row or a positional operand
        assert cli_name in compare_flags, (
            f"MCP_CLI_NAME_MAP[{mcp_param!r}] = {cli_name!r} is not a real "
            "`compare` flag"
        )


def test_gate_flags_unmapped_mcp_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D10.3 is not a no-op: an ``abi_compare`` param missing from the map fails."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_options.py").write_text(
        "MCP_CLI_NAME_MAP = {'old_input': '--old'}\n"
    )
    (pkg / "mcp_server.py").write_text(
        "def abi_compare(old_input, new_input, mystery_param='x'):\n    return ''\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate._check_mcp_cli_name_map(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    # new_input and mystery_param are both absent from the planted map.
    assert any("new_input" in m for m in msgs)
    assert any("mystery_param" in m for m in msgs)


# ── D8: --ast-frontend (legacy --header-backend aliases removed) ─────────────


@pytest.mark.parametrize("cmd_name", ["compare", "dump"])
def test_ast_frontend_is_the_only_frontend_spelling(cmd_name: str) -> None:
    """``--ast-frontend`` is the frontend flag; the removed ``--header-backend``
    alias is gone (clean removal, ADR-037 D7/D8)."""
    cmd = _registered_commands()[cmd_name]
    by_dest = {p.name: p for p in cmd.params}  # type: ignore[attr-defined]
    param = by_dest["header_backend"]
    assert "--ast-frontend" in param.opts
    assert "--header-backend" not in param.opts


def test_per_side_ast_frontend_has_no_legacy_alias() -> None:
    """Per-side ``--old/new-ast-frontend`` carry no legacy ``--*-header-backend``."""
    cmd = _registered_commands()["compare"]
    by_dest = {p.name: p for p in cmd.params}  # type: ignore[attr-defined]
    for dest, new, old in (
        ("old_header_backend", "--old-ast-frontend", "--old-header-backend"),
        ("new_header_backend", "--new-ast-frontend", "--new-header-backend"),
    ):
        assert new in by_dest[dest].opts
        assert old not in by_dest[dest].opts


def test_legacy_header_backend_flag_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The removed ``--header-backend`` spelling is now a hard usage error."""
    from click.testing import CliRunner

    from abicheck.cli import main

    old_p = _make_snap_file(tmp_path, "libdn", "1.0", [_func("a")])
    new_p = _make_snap_file(tmp_path, "libdn", "2.0", [_func("a")])
    res = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--header-backend", "castxml"]
    )
    assert res.exit_code != 0
    assert "no such option" in res.output.lower() or "No such option" in res.output


# ── D8: --ast-frontend unifies L2 header AST + L4 source-ABI extractor ────────


def test_ast_frontend_threads_to_l4_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--ast-frontend` selects the L4 source-ABI replay extractor too, not just
    the L2 header AST — one frontend choice across both stages (ADR-037 D8)."""
    import abicheck.buildsource.inline as inline
    import abicheck.cli_buildsource as cb
    from abicheck.model import AbiSnapshot

    captured: dict[str, object] = {}

    def _fake_collect(**kwargs: object) -> None:
        captured.update(kwargs)
        return None

    # embed_build_source imports collect_inline_pack from the inline module at
    # call time, so patch it at the source.
    monkeypatch.setattr(inline, "collect_inline_pack", _fake_collect)
    src = tmp_path / "src"
    src.mkdir()
    snap = AbiSnapshot(library="l", version="1")
    cb.embed_build_source(snap, None, src, collect_mode="source-target", extractor="clang")
    assert captured.get("extractor") == "clang"


@pytest.mark.parametrize("name", ["dump", "scan"])
def test_project_config_flag_is_config_not_build_config(name: str) -> None:
    """`--build-config` was renamed to `--config` (ADR-037 D4) to match `compare`
    and reflect that it loads the whole project .abicheck.yml. No back-compat
    window is kept, so the old spelling must be gone on dump/scan."""
    commands = _registered_commands()
    flags = _command_flags(commands[name])
    assert "--config" in flags, name
    assert "--build-config" not in flags, name  # old spelling fully removed


# ── Resolved option-set snapshot (catches an accidental flag drop in review) ──

# Frozen sets of every option spelling each verdict-emitting command exposes.
# A diff here in review means a flag was added or dropped — update deliberately.
_OPTION_SET_SNAPSHOT: dict[str, tuple[str, ...]] = {
    "compare": (
        "--annotate", "--annotate-additions", "--ast-frontend", "--btf", "--bundle-cohort", "--bundle-system-providers",
        "--collapse-versioned-symbols", "--config", "--ctf", "--debug-format", "--debug-info1",
        "--debug-info2", "--debug-root", "--debug-root1", "--debug-root2", "--debuginfod", "--debuginfod-url",
        "--demangle", "--depth", "--devel-pkg1", "--devel-pkg2", "--dso-only", "--dwarf",
        "--dwarf-only", "--exit-code-scheme", "--explain-patterns", "--fail-on-removed-library", "--follow-deps", "--format",
        "--gcc-option", "--gcc-options", "--gcc-path", "--gcc-prefix", "--header", "--include",
        "--include-private-dso", "--jobs", "--keep-extracted", "--lang", "--ld-library-path", "--manifest",
        "--max", "--new-ast-frontend", "--new-build-info", "--new-header", "--new-include", "--new-pdb-path",
        "--new-sources", "--new-version", "--no-bundle-analysis", "--no-demangle", "--no-fail-on-removed-library", "--no-nostdinc",
        "--no-pattern-verdicts", "--no-scope-public-headers", "--nostdinc", "--old-ast-frontend", "--old-build-info", "--old-header",
        "--old-include", "--old-pdb-path", "--old-sources", "--old-version", "--output", "--output-dir",
        "--pattern-verdicts", "--pdb-path", "--policy", "--policy-file", "--probe-matrix-new", "--probe-matrix-old",
        "--public-symbol", "--public-symbols-list", "--recommend", "--report-mode", "--require-justification", "--scope-public-headers",
        "--search-path", "--severity-abi-breaking", "--severity-addition", "--severity-potential-breaking", "--severity-preset", "--severity-quality-issues",
        "--show-filtered", "--show-impact", "--show-only", "--show-redundant", "--stat", "--strict-suppressions",
        "--suppress", "--surface-metrics", "--sysroot", "--verbose", "-H", "-I",
        "-j", "-o", "-v",
    ),
    "appcompat": (
        "--check-against", "--format", "--header", "--include", "--lang",
        "--list-required-symbols", "--new-header", "--new-include", "--new-version",
        "--no-scope-public-headers", "--old-header", "--old-include", "--old-version",
        "--output", "--policy", "--policy-file", "--scope-public-headers",
        "--severity-abi-breaking", "--severity-addition", "--severity-potential-breaking",
        "--severity-preset", "--severity-quality-issues", "--show-irrelevant", "--suppress",
        "--verbose", "-H", "-I", "-o", "-v",
    ),
}


@pytest.mark.parametrize("cmd_name", sorted(_OPTION_SET_SNAPSHOT))
def test_option_set_snapshot(cmd_name: str) -> None:
    """Each command's full option surface matches the frozen snapshot."""
    commands = _registered_commands()
    flags = _command_flags(commands[cmd_name])
    assert sorted(flags) == sorted(_OPTION_SET_SNAPSHOT[cmd_name])


# ── Chokepoint parity: one classifier, no scope_public drift ─────────────────


def _make_snap_file(
    tmp_path: Path, name: str, version: str, funcs: list[Function]
) -> Path:
    snap = AbiSnapshot(library=name, version=version, functions=funcs)
    p = tmp_path / f"{name}_{version}.json"
    save_snapshot(snap, p)
    return p


def _func(name: str) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_extern_c=True,
    )


def test_compare_release_matches_service_run_compare(tmp_path: Path) -> None:
    """``compare-release``'s per-pair runner classifies identically to
    ``service.run_compare`` — they share the one chokepoint (ADR-037 D1)."""
    from abicheck import service
    from abicheck.cli_compare_release import _run_compare_pair

    old_p = _make_snap_file(tmp_path, "libfoo", "1.0", [_func("foo"), _func("bar")])
    new_p = _make_snap_file(tmp_path, "libfoo", "2.0", [_func("foo")])

    svc_result, _, _ = service.run_compare(old_p, new_p, scope_to_public_surface=True)
    rel_result, _, _ = _run_compare_pair(
        old_p,
        new_p,
        old_headers=[],
        new_headers=[],
        old_includes=[],
        new_includes=[],
        old_version="",
        new_version="",
        lang="c++",
        suppress=None,
        policy="strict_abi",
        policy_file_path=None,
        old_pdb_path=None,
        new_pdb_path=None,
        scope_to_public_surface=True,
    )

    assert svc_result.verdict == rel_result.verdict
    assert sorted(c.kind for c in svc_result.breaking) == sorted(
        c.kind for c in rel_result.breaking
    )
    assert sorted(c.kind for c in svc_result.source_breaks) == sorted(
        c.kind for c in rel_result.source_breaks
    )
    assert sorted(c.kind for c in svc_result.compatible) == sorted(
        c.kind for c in rel_result.compatible
    )


def test_run_compare_request_equivalent_to_kwargs_shim(tmp_path: Path) -> None:
    """The kwargs ``run_compare`` shim and a hand-built ``CompareRequest`` agree."""
    from abicheck.api_types import CompareRequest, InputSpec
    from abicheck.service import run_compare, run_compare_request

    old_p = _make_snap_file(tmp_path, "libbar", "1.0", [_func("a"), _func("b")])
    new_p = _make_snap_file(tmp_path, "libbar", "2.0", [_func("a")])

    shim_result, _, _ = run_compare(old_p, new_p)
    req = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
    req_result, _, _ = run_compare_request(req)

    assert shim_result.verdict == req_result.verdict
    assert sorted(c.kind for c in shim_result.breaking) == sorted(
        c.kind for c in req_result.breaking
    )


def test_run_compare_request_normalizes_lang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted upper-case ``lang`` is lowered before snapshot resolution.

    ``validate()`` accepts ``"C"`` case-insensitively, but the ELF dump path
    does case-sensitive ``lang == "c"`` checks — ``run_compare_request`` must
    normalise so ``"C"`` is not silently treated as C++.
    """
    from abicheck import service
    from abicheck.api_types import CompareRequest, InputSpec

    old_p = _make_snap_file(tmp_path, "liblang", "1.0", [_func("a")])
    new_p = _make_snap_file(tmp_path, "liblang", "2.0", [_func("a")])

    seen_langs: list[str] = []

    def _spy_resolve_input(path, headers, includes, version, lang, **kwargs):  # type: ignore[no-untyped-def]
        seen_langs.append(lang)
        return AbiSnapshot(library="liblang", version=version)

    monkeypatch.setattr(service, "resolve_input", _spy_resolve_input)

    req = CompareRequest(
        old=InputSpec.of(old_p), new=InputSpec.of(new_p), lang="C"
    )
    service.run_compare_request(req)

    assert seen_langs == ["c", "c"]
