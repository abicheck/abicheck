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
    import abicheck.cli_compare_release  # noqa: F401  — registers `compare-release`
    import abicheck.cli_max  # noqa: F401  — registers `deep-compare`
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


def test_intentional_subset_decorator_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`deep-compare` omitting `@severity_options` is allowlisted, not an error."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_max.py").write_text(
        "import click\n"
        '@main.command("deep-compare")\n'
        "@two_sided_input_options\n"
        "@policy_options\n"
        "@scope_options\n"
        "@output_options(['json'])\n"
        "def deep_compare_cmd():\n"
        "    pass\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert not any("deep-compare" in m and "severity_options" in m for m in msgs), msgs


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


def test_deferred_multi_default_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flag on the ``DEFERRED_MULTI_DEFAULT`` allowlist is skipped (Phase 3)."""
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
    assert [m for c, m in findings.errors if c == "cli-contract"] == []


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
    assert gate._DEFERRED_MULTI_DEFAULT_FLAGS == co.DEFERRED_MULTI_DEFAULT


# ── Resolved option-set snapshot (catches an accidental flag drop in review) ──

# Frozen sets of every option spelling each verdict-emitting command exposes.
# A diff here in review means a flag was added or dropped — update deliberately.
_OPTION_SET_SNAPSHOT: dict[str, tuple[str, ...]] = {
    "compare": (
        "--annotate", "--annotate-additions", "--btf", "--collapse-versioned-symbols",
        "--collect-mode", "--ctf", "--debug-format", "--debug-root", "--debug-root1",
        "--debug-root2", "--debuginfod", "--debuginfod-url", "--demangle", "--dwarf",
        "--dwarf-only", "--explain-patterns", "--follow-deps", "--format", "--header",
        "--header-backend", "--include", "--lang", "--ld-library-path", "--new-build-info",
        "--new-header", "--new-header-backend", "--new-include", "--new-pdb-path",
        "--new-sources", "--new-version", "--no-demangle", "--no-pattern-verdicts",
        "--no-scope-public-headers", "--old-build-info", "--old-header",
        "--old-header-backend", "--old-include", "--old-pdb-path", "--old-sources",
        "--old-version", "--output", "--pattern-verdicts", "--pdb-path", "--policy",
        "--policy-file", "--probe-matrix-new", "--probe-matrix-old", "--public-symbol",
        "--public-symbols-list", "--recommend", "--report-mode", "--require-justification",
        "--scope-public-headers", "--search-path", "--severity-abi-breaking",
        "--severity-addition", "--severity-potential-breaking", "--severity-preset",
        "--severity-quality-issues", "--show-filtered", "--show-impact", "--show-only",
        "--show-redundant", "--stat", "--strict-suppressions", "--suppress",
        "--surface-metrics", "--verbose", "-H", "-I", "-o", "-v",
    ),
    "compare-release": (
        "--annotate", "--annotate-additions", "--bundle-cohort", "--bundle-system-providers",
        "--debug-info1", "--debug-info2", "--devel-pkg1", "--devel-pkg2", "--dso-only",
        "--fail-on-removed-library", "--format", "--header", "--include",
        "--include-private-dso", "--jobs", "--keep-extracted", "--lang", "--manifest",
        "--new-header", "--new-include", "--new-version", "--no-bundle-analysis",
        "--no-fail-on-removed-library", "--no-scope-public-headers", "--old-header",
        "--old-include", "--old-version", "--output", "--output-dir", "--policy",
        "--policy-file", "--probe-matrix-new", "--probe-matrix-old", "--require-justification",
        "--scope-public-headers", "--severity-abi-breaking", "--severity-addition",
        "--severity-potential-breaking", "--severity-preset", "--severity-quality-issues",
        "--strict-suppressions", "--suppress", "--verbose", "-H", "-I", "-j", "-o", "-v",
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
    "deep-compare": (
        "--depth", "--format", "--header", "--header-backend", "--include",
        "--keep-snapshots", "--lang", "--max", "--new-build-info", "--new-header",
        "--new-header-backend", "--new-include", "--new-sources", "--new-version",
        "--no-scope-public-headers", "--old-build-info", "--old-header",
        "--old-header-backend", "--old-include", "--old-sources", "--old-version",
        "--output", "--policy", "--policy-file", "--recommend", "--scope-public-headers",
        "--severity-preset", "--sources", "--suppress", "--verbose", "-H", "-I", "-o", "-v",
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
