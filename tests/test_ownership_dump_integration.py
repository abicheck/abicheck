"""The ownership classifier's contract, re-asserted through a real
``abicheck dump`` / ``compare`` (ADR-075; target-ownership plan Phase 2).

Phase 1 stated the precedence rules as properties of the pure classifier
(``tests/test_ownership_classifier.py``). These tests state the same rules
about what a real castxml dump *records*, reading the stored snapshot back:

* rule-order independence -- the same rules listed in another order give
  the same decisions and the same fingerprint;
* the most specific root wins -- a vendored dependency inside the target
  root is the dependency's;
* ``-I`` never grants ownership -- a header reached only through ``-I`` is
  ``unresolved``, never target;
* a system prefix never beats an explicit root -- a target installed under
  a ``usr/include`` path stays target, while a real toolchain declaration
  stays toolchain.

Plus the comparability refusal and the unchanged-verdict half of ADR-075
D3, through the CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.model.extraction_scope import ownership_of
from abicheck.serialization import load_snapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ELF fixture"),
]


def _require(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


def _abicheck(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck", *args],
        cwd=cwd,
        env={**os.environ, "ABICHECK_AST_FRONTEND": "castxml"},
        capture_output=True,
        text=True,
    )


# The target lives under `usr/include/lib/` inside the project on purpose: a
# path the system heuristic would call toolchain, claimed by an explicit
# root. `vendor/` is a dependency vendored *inside* the target root; `extra/`
# is reached only through `-I`.
_FILES = {
    "usr/include/lib/api.h": (
        "#pragma once\n"
        '#include "vendor/dep.h"\n'
        '#include "extra.h"\n'
        "#include <stdio.h>\n"
        "int lib_api(dep_t d, extra_t e);\n"
        "namespace lib { namespace detail { int hidden(); } }\n"
    ),
    "usr/include/lib/vendor/dep.h": (
        "#pragma once\ntypedef struct dep { int x; } dep_t;\nint dep_fn(void);\n"
    ),
    "extra/extra.h": "#pragma once\ntypedef struct extra { int y; } extra_t;\nint extra_fn(void);\n",
}
_SOURCE = (
    '#include "api.h"\n'
    "int lib_api(dep_t d, extra_t e) { return d.x + e.y; }\n"
    "int dep_fn(void) { return 1; } int extra_fn(void) { return 2; }\n"
    "namespace lib { namespace detail { int hidden() { return 3; } } }\n"
)

_CONFIG_A = """\
scope:
  public_header_dirs: [usr/include/lib]
  private_namespaces: [lib::detail]
  dependencies:
    - name: dep
      header_roots: [usr/include/lib/vendor]
    - name: unused
      header_roots: [nowhere]
"""
# The same rules, every list in another order.
_CONFIG_B = """\
scope:
  dependencies:
    - name: unused
      header_roots: [nowhere]
    - name: dep
      header_roots: [usr/include/lib/vendor/]
  private_namespaces: [lib::detail]
  public_header_dirs: [./usr/include/lib]
"""


def _project(root: Path, config: str) -> Path:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / "lib.cpp").write_text(_SOURCE)
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-Iusr/include/lib", "-Iextra", "-o", "liblib.so", "lib.cpp"],
        check=True,
        cwd=root,
    )
    (root / ".abicheck.yml").write_text(config)
    return root


def _dump(root: Path, out: str, *extra: str) -> Path:
    result = _abicheck(
        ["dump", "liblib.so", "-H", "usr/include/lib", "-I", "extra", "-o", out, *extra],
        root,
    )
    assert result.returncode == 0, result.stderr
    return root / out


def _decisions(path: Path) -> dict[str, tuple[str, str]]:
    snap = load_snapshot(path)
    out = {}
    for decl in (*snap.functions, *snap.types):
        decision = ownership_of(decl)
        if decision is not None:
            out[decl.name] = (decision.owner, decision.contract)
    return out


@pytest.fixture(scope="module")
def dumped(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    _require("g++", "castxml")
    a = _project(tmp_path_factory.mktemp("own_a"), _CONFIG_A)
    b = _project(tmp_path_factory.mktemp("own_b"), _CONFIG_B)
    return {
        "a": _dump(a, "snap.json"),
        "b": _dump(b, "snap.json"),
        # Toolchain declarations are dropped by dependency scoping unless
        # asked for; this dump keeps them so the system-prefix rule has a
        # real toolchain declaration to classify.
        "a_system": _dump(a, "snap_system.json", "--include-system-declarations"),
        "root_a": a,
    }


def test_real_dump_records_scope_and_decisions(dumped: dict[str, Path]) -> None:
    snap = load_snapshot(dumped["a"])
    assert snap.extraction_scope is not None
    rules = snap.extraction_scope.ownership_rules
    # Recorded relative to the project root, POSIX, normalized.
    assert rules.target_roots == ("usr/include/lib",)
    assert snap.extraction_scope.dependency_evidence == "full"


def test_most_specific_root_wins(dumped: dict[str, Path]) -> None:
    decisions = _decisions(dumped["a"])
    assert decisions["dep_fn"] == ("dependency:dep", "external")
    assert decisions["lib_api"] == ("target", "public")


def test_private_namespace_narrows_contract_only(dumped: dict[str, Path]) -> None:
    decisions = _decisions(dumped["a"])
    hidden = next(v for k, v in decisions.items() if k.endswith("hidden"))
    assert hidden == ("target", "private")


def test_include_path_never_grants_ownership(dumped: dict[str, Path]) -> None:
    decisions = _decisions(dumped["a"])
    assert decisions["extra_fn"] == ("unresolved", "unresolved")


def test_system_prefix_never_beats_an_explicit_root(dumped: dict[str, Path]) -> None:
    decisions = _decisions(dumped["a_system"])
    # The target sits under a `usr/include` path and is still target ...
    assert decisions["lib_api"][0] == "target"
    # ... while a real toolchain declaration pulled in by <stdio.h> is not.
    toolchain = [k for k, v in decisions.items() if v[0] == "toolchain"]
    assert toolchain, "expected <stdio.h> declarations to classify as toolchain"


def test_rule_order_is_irrelevant(dumped: dict[str, Path]) -> None:
    a, b = load_snapshot(dumped["a"]), load_snapshot(dumped["b"])
    assert a.extraction_scope is not None and b.extraction_scope is not None
    assert a.extraction_scope.fingerprint == b.extraction_scope.fingerprint
    assert _decisions(dumped["a"]) == _decisions(dumped["b"])


def test_equal_scopes_compare_clean(dumped: dict[str, Path]) -> None:
    result = _abicheck(
        ["compare", str(dumped["a"]), str(dumped["b"]), "-o", "json=r.json"],
        dumped["root_a"],
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((dumped["root_a"] / "r.json").read_text())
    assert report["verdict"] == "NO_CHANGE"
    assert "extraction scope" not in json.dumps(report.get("coverage_warnings", []))


def test_differing_dependency_evidence_is_refused(dumped: dict[str, Path], tmp_path: Path) -> None:
    doc = json.loads(dumped["b"].read_text())
    scope = _find_block(doc, "extraction_scope")
    scope["dependency_evidence"] = "referenced"
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(doc))
    result = _abicheck(["compare", str(dumped["a"]), str(edited)], dumped["root_a"])
    assert result.returncode != 0
    assert "dependency_evidence" in result.stderr + result.stdout


def test_unrecorded_baseline_is_compared_with_a_note(dumped: dict[str, Path], tmp_path: Path) -> None:
    doc = json.loads(dumped["a"].read_text())
    _drop_block(doc, "extraction_scope")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(doc))
    result = _abicheck(
        ["compare", str(legacy), str(dumped["b"]), "-o", f"json={tmp_path / 'r.json'}"],
        dumped["root_a"],
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["verdict"] == "NO_CHANGE"
    assert "predates extraction-scope recording" in json.dumps(report)


def _find_block(doc: object, key: str) -> dict:
    if isinstance(doc, dict):
        if isinstance(doc.get(key), dict):
            return doc[key]
        for value in doc.values():
            found = _maybe(value, key)
            if found is not None:
                return found
    raise KeyError(key)


def _maybe(doc: object, key: str) -> dict | None:
    try:
        return _find_block(doc, key)
    except KeyError:
        return None


def _drop_block(doc: object, key: str) -> None:
    if isinstance(doc, dict):
        doc.pop(key, None)
        for value in doc.values():
            _drop_block(value, key)


def test_typed_api_classifies_exactly_as_the_cli(dumped: dict[str, Path], monkeypatch) -> None:
    """Front-end parity (``tests/CLAUDE.md``): a typed ``DumpRequest`` whose
    ``InputSpec.ownership`` carries the same project rules decides every
    declaration the way ``abicheck dump`` did."""
    from abicheck.service_dump_pipeline import run_dump_request
    from abicheck.workflows.contracts import DumpRequest
    from abicheck.workflows.extraction import load_build_config
    from abicheck.workflows.ownership_request import ownership_request_from_config
    from abicheck.workflows.request_inputs import InputSpec

    root = dumped["root_a"]
    monkeypatch.chdir(root)
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "castxml")
    cfg = load_build_config(root / ".abicheck.yml")
    request = DumpRequest(
        input=InputSpec(
            path=root / "liblib.so",
            headers=(root / "usr/include/lib",),
            includes=(root / "extra",),
            ownership=ownership_request_from_config(cfg, root),
        )
    )
    snap = run_dump_request(request)
    api = {
        d.name: (o.owner, o.contract)
        for d in (*snap.functions, *snap.types)
        if (o := ownership_of(d)) is not None
    }
    assert api == _decisions(dumped["a"])
    assert snap.extraction_scope is not None
    assert snap.extraction_scope.fingerprint == load_snapshot(dumped["a"]).extraction_scope.fingerprint  # type: ignore[union-attr]
