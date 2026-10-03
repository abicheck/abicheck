# SPDX-License-Identifier: Apache-2.0
"""``compare --no-baseline`` honours ``.abicheck.yml`` as two-sided ``compare`` does.

Bug class: a setting two-sided ``compare`` reads off the resolved project
config, that the audit path resolves too and then never reads. Every one of
``--lang``, the debug flags, ``--pdb-path``, ``--source-method`` and
``--public-symbol`` was demoted from the CLI to a config key (ADR-040,
ADR-068 Phase 7), so on ``--no-baseline`` -- which reads none of them -- the
setting had no effect at all: a ``compile.lang: c`` project was audited as
C++, the ``debug:`` block never reached the candidate, and
``scope.public_symbols`` forced nothing public. ``test_compare_no_baseline_
options.py`` guards CLI options; nothing guarded config fields.

Two invariants:

* every field of the resolved config is read by the audit, or declared not
  applicable or not yet wired, each with its reason -- so the next field is
  accounted for, not silently dropped;
* over generated project configs, the values the audit hands its resolver
  and its comparison are the ones the config states. The oracle is the
  written config document, never ``config_run_settings``.

The audit now also hands the resolver ``notify``, as two-sided ``compare``
does. Warning-level notes already reached stderr through the logger without
it; the difference is info-level notes such as a resolved detached debug
file, which need a compiled ELF to observe, so this module checks only that
the callback is passed.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import random
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from abicheck.cli import main as abicheck_main
from abicheck.cli_helpers_compare import ResolvedCompareConfig
from abicheck.model.evidence_depth_levels import SourceMethod, method_to_collect_mode
from abicheck.workflows import no_baseline_compare as nbc

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import example_catalog  # noqa: E402

_AUDIT_MODULES = (
    ROOT / "abicheck/frontends/cli/commands/compare_no_baseline.py",
    # One-comparison-product F-23: the resolution both the scalar and the
    # directory audit share (`resolve_no_baseline_invocation`).
    ROOT / "abicheck/frontends/cli/commands/no_baseline_invocation.py",
    ROOT / "abicheck/frontends/cli/commands/no_baseline_rulings.py",
)
_SETTINGS_MODULE = ROOT / "abicheck/frontends/cli/compare_config_settings.py"

_GATE = (
    "the audit gate is opted into by --severity-preset alone and its rule reads "
    "kind membership, not severity categories (ADR-068 2026-09-10 amendment)"
)
_RELEASE = (
    "directory/package release fan-out only: a two-sided release setting "
    "with no meaning for a candidate audited alone"
)
NOT_APPLICABLE: dict[str, str] = {
    "severity": _GATE,
    "severity_active": _GATE,
    "merged_severity_preset": _GATE,
    "merged_severity_abi_breaking": _GATE,
    "merged_severity_potential_breaking": _GATE,
    "merged_severity_quality_issues": _GATE,
    "merged_severity_addition": _GATE,
    "exit_code_scheme": _GATE,
    "bundle_system_providers": _RELEASE,
    "bundle_cohorts": _RELEASE,
    "release_support_promise": _RELEASE,
    "fail_on_removed_library": _RELEASE,
    "resource_limits_max_bundle_facts_decode_nodes": (
        "bounds decoding a bundle-facts document, which --no-baseline does not "
        "accept as its operand"
    ),
}
#: Shrink-only: a field the audit should read and does not yet.
NOT_YET_WIRED: dict[str, str] = {
    "show_redundant": (
        "restores deduplicated findings through the two-sided finalizer; the "
        "audit's report has no deduplicated set to restore into yet"
    ),
}


def _config_fields() -> set[str]:
    props = {
        n for n, v in vars(ResolvedCompareConfig).items() if isinstance(v, property)
    }
    return {f.name for f in dataclasses.fields(ResolvedCompareConfig)} | props


def _reads(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "resolved_cfg"
    }


def _audit_reads() -> set[str]:
    read = set().union(*map(_reads, _AUDIT_MODULES))
    calls_settings = any(
        "config_run_settings(resolved_cfg)" in p.read_text(encoding="utf-8")
        for p in _AUDIT_MODULES
    )
    return read | (_reads(_SETTINGS_MODULE) if calls_settings else set())


def test_every_config_field_is_read_or_declared() -> None:
    fields = _config_fields()
    read = _audit_reads()
    declared = set(NOT_APPLICABLE) | set(NOT_YET_WIRED)
    assert not (set(NOT_APPLICABLE) & set(NOT_YET_WIRED))
    assert not (read & declared), "a declared field is now read: drop it"
    assert not (declared - fields), "a declared field no longer exists"
    assert fields - read - declared == set(), (
        "read it on --no-baseline, or declare it with a reason"
    )
    assert all(len(r) > 30 for r in (*NOT_APPLICABLE.values(), *NOT_YET_WIRED.values()))


def test_the_settings_module_reads_what_two_sided_compare_needs() -> None:
    """The shared reader covers the demoted settings, and two-sided
    ``compare`` reads them through it rather than a second copy."""
    assert {
        "compile_lang",
        "debug_format",
        "dwarf_only",
        "debuginfod",
        "debuginfod_url",
        "pdb_path",
        "source_method",
    } <= _reads(_SETTINGS_MODULE)
    two_sided = (ROOT / "abicheck/cli_compare_helpers.py").read_text(encoding="utf-8")
    assert "config_run_settings(resolved_cfg)" in two_sided
    for field in (
        "compile_lang",
        "debug_format",
        "dwarf_only",
        "debuginfod",
        "pdb_path",
    ):
        assert f"resolved_cfg.{field}" not in two_sided, field


@pytest.fixture
def candidate() -> Path:
    return (
        example_catalog.case_dir("case143_audit_accidental_export")
        / "snapshot.abi.json"
    )


_LANGS = (None, "c", "c++")
_FORMATS = (None, "auto", "dwarf", "btf")
_BOOLS = (None, True, False)
_URLS = (None, "https://debuginfod.example")
_PDBS = (None, "lib.pdb")
_PUBLIC = (None, ["only_this"], ["a", "b"])
_METHODS = (None, "s1", "s3", "s6")


def _document(lang, fmt, dwarf_only, debuginfod, url, pdb, public, method) -> dict:
    doc: dict = {}
    if lang is not None:
        doc.setdefault("compile", {})["lang"] = lang
    debug = {
        k: v
        for k, v in (
            ("format", fmt),
            ("dwarf_only", dwarf_only),
            ("debuginfod", debuginfod),
            ("debuginfod_url", url),
            ("pdb_path", pdb),
        )
        if v is not None
    }
    if debug:
        doc["debug"] = debug
    if public is not None:
        doc.setdefault("scope", {})["public_symbols"] = public
    if method is not None:
        doc["source"] = {"method": method}
    return doc


_ALL = list(
    itertools.product(_LANGS, _FORMATS, _BOOLS, _BOOLS, _URLS, _PDBS, _PUBLIC, _METHODS)
)
_SAMPLE = random.Random(20261003).sample(_ALL, 36) + [(None,) * 8]


@pytest.mark.parametrize("combo", _SAMPLE)
def test_the_audit_applies_the_config_it_resolved(
    combo, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lang, fmt, dwarf_only, debuginfod, url, pdb, public, method = combo
    (tmp_path / ".abicheck.yml").write_text(
        yaml.safe_dump(_document(*combo)), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    seen: dict[str, dict] = {}

    def spy(name, real):
        def wrapper(*args, **kwargs):
            seen[name] = kwargs
            return real(*args, **kwargs)

        return wrapper

    # Both are called from the one candidate-audit sequence the scalar and
    # the directory audit share (`audit_no_baseline_candidate`).
    monkeypatch.setattr(
        nbc,
        "resolve_no_baseline_candidate",
        spy("resolve", nbc.resolve_no_baseline_candidate),
    )
    monkeypatch.setattr(
        nbc, "run_no_baseline_compare", spy("run", nbc.run_no_baseline_compare)
    )
    result = CliRunner().invoke(
        abicheck_main, ["compare", "--no-baseline", str(candidate), "-o", "json=-"]
    )
    assert result.exit_code in (0, 1, 2, 4), result.output
    resolve, run = seen["resolve"], seen["run"]
    assert resolve["lang"] == (lang or "c++")
    assert resolve["lang_explicit"] is (lang is not None)
    assert resolve["debug_format"] == (None if fmt in (None, "auto") else fmt)
    assert resolve["dwarf_only"] is bool(dwarf_only)
    assert resolve["enable_debuginfod"] is bool(debuginfod)
    assert resolve["debuginfod_url"] == url
    assert resolve["pdb"] == (Path(pdb) if pdb else None)
    expected_mode = (
        method_to_collect_mode(SourceMethod(method)) if method is not None else "off"
    )
    assert resolve["collect_mode"] == expected_mode
    assert callable(resolve["notify"])
    assert run["force_public_symbols"] == (set(public) if public else None)
