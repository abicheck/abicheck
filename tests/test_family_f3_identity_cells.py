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

"""Harness H3, design-hardening Phase 3 exit -- one cell per identity
function the H3 inventory (``tests/test_family_f3_identity.py``'s
``COVERAGE``) used to list as ``UNCOVERED``.

Each cell states the identity's own environment contract with an oracle
independent of the implementation: the *same* thing spelled differently
(path spelling, rule order, prose, mangling scheme, hash seed, relocated
storage) keys equal, and a *different* thing keys apart (the negative
control that keeps every cell from passing vacuously). Entity identities a
declaring path can reach are celled in the main module's path probes
(``test_declaring_path_identity_is_root_relative``).

The registry :data:`tests._family_f3_catalogue.ENVIRONMENT_CELLS` names
every function celled here; ``test_every_environment_cell_has_a_test``
keeps the registry and this module in step.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _family_f3_catalogue as cat

REPO = Path(__file__).resolve().parent.parent


def test_every_environment_cell_has_a_test() -> None:
    names = {n for n in globals() if n.startswith("test_cell_")}
    want = {"test_cell_" + q.rsplit(":", 1)[1] for q in cat.ENVIRONMENT_CELLS}
    assert want <= names, sorted(want - names)


# -- snapshot-local tier ------------------------------------------------------


def test_cell_snapshot_local_identity() -> None:
    """Keyed on the spelling extraction hands it -- the codec's *decoded*
    spelling -- so a Mach-O-decorated and a plain spelling of one name are
    one identity, and two distinct names never are."""
    from abicheck.model.identity_tiers import snapshot_local_identity
    from abicheck.model.name_decoration import macho

    for name in cat.ITANIUM_NAMES:
        decorated = macho.encode(name)
        assert snapshot_local_identity(macho.decode_itanium(decorated)) == (
            snapshot_local_identity(name)
        )
    keys = {snapshot_local_identity(n) for n in cat.ITANIUM_NAMES}
    assert len(keys) == len(set(cat.ITANIUM_NAMES))


# -- extraction scope ---------------------------------------------------------


def _scope(roots: tuple[str, ...], private: tuple[str, ...] = ()):
    from abicheck.model.extraction_scope import ExtractionScope
    from abicheck.model.ownership_rules import OwnershipRules

    return ExtractionScope(
        ownership_rules=OwnershipRules(target_roots=roots, private_headers=private)
    )


def test_cell_extraction_scope_identity() -> None:
    """Rule *order* is not identity (the canonical form sorts); a different
    rule set is; an agreeing pair is one value, a disagreeing pair names
    both sides."""
    from abicheck.model.extraction_scope import extraction_scope_identity

    a = _scope(("include", "src/public"))
    a_reordered = _scope(("src/public", "include"))
    b = _scope(("include",))
    assert extraction_scope_identity(a, a_reordered) == a.fingerprint
    assert extraction_scope_identity(a, b) != extraction_scope_identity(a, a)
    assert extraction_scope_identity(None, a, has_old=False) == a.fingerprint


def test_cell_snapshot_scope_identity() -> None:
    from abicheck.model import AbiSnapshot
    from abicheck.model.extraction_scope import (
        extraction_scope_identity,
        snapshot_scope_identity,
    )

    old = AbiSnapshot(library="l", version="1")
    new = AbiSnapshot(library="l", version="2")
    old.extraction_scope = _scope(("include", "src"))
    new.extraction_scope = _scope(("src", "include"))
    assert snapshot_scope_identity(old, new) == extraction_scope_identity(
        old.extraction_scope, new.extraction_scope
    )
    assert snapshot_scope_identity(old, new) == new.extraction_scope.fingerprint
    new.extraction_scope = _scope(("include",))
    assert "|" in snapshot_scope_identity(old, new)


# -- header exclusions --------------------------------------------------------


_PATTERNS = ("*/detail/*.h", "internal_*.h")


def test_cell_canonical_exclusion_identity() -> None:
    from abicheck.model.header_exclusion_record import canonical_exclusion_identity

    base = canonical_exclusion_identity(_PATTERNS)
    assert canonical_exclusion_identity(tuple(reversed(_PATTERNS))) == base
    assert canonical_exclusion_identity((*_PATTERNS, _PATTERNS[0])) == base
    assert canonical_exclusion_identity(_PATTERNS[:1]) != base
    assert canonical_exclusion_identity(()) == ""


def test_cell_comparison_exclusion_identity() -> None:
    from abicheck.model import AbiSnapshot
    from abicheck.model.header_exclusion_record import comparison_exclusion_identity

    def snap(patterns: tuple[str, ...]) -> AbiSnapshot:
        s = AbiSnapshot(library="l", version="1")
        s.excluded_header_patterns = patterns
        return s

    agreeing = comparison_exclusion_identity(
        snap(_PATTERNS), snap(tuple(reversed(_PATTERNS)))
    )
    assert agreeing == comparison_exclusion_identity(snap(_PATTERNS), snap(_PATTERNS))
    one_sided = comparison_exclusion_identity(snap(()), snap(_PATTERNS))
    other_side = comparison_exclusion_identity(snap(_PATTERNS), snap(()))
    assert len({agreeing, one_sided, other_side}) == 3


def test_cell_release_exclusion_identity() -> None:
    from abicheck.model.header_exclusion_record import (
        canonical_exclusion_identity,
        release_exclusion_identity,
    )

    ident = canonical_exclusion_identity(_PATTERNS)
    other = canonical_exclusion_identity(_PATTERNS[:1])
    assert release_exclusion_identity([ident, ident], "") == release_exclusion_identity(
        [ident], ""
    )
    assert release_exclusion_identity([ident, other], "") != release_exclusion_identity(
        [ident], ""
    )
    assert release_exclusion_identity([ident, other], "") == release_exclusion_identity(
        [other, ident], ""
    )


# -- policy rules and built-in configuration ----------------------------------


def test_cell_rule_identity() -> None:
    """Prose never reaches the identity; a selector or gate does; a
    separator inside a selector cannot forge a second field."""
    from abicheck.policy.rule_identity import rule_identity
    from abicheck.suppression import Suppression

    base = Suppression(symbol_pattern="foo.*", reason="first wording")
    assert rule_identity(Suppression(symbol_pattern="foo.*", reason="reworded")) == (
        rule_identity(base)
    )
    assert rule_identity(Suppression(symbol_pattern="bar.*")) != rule_identity(base)
    forged = Suppression(symbol_pattern="a|change_kind=func_removed")
    split = Suppression(symbol_pattern="a", change_kind="func_removed")
    assert rule_identity(forged) != rule_identity(split)
    assert rule_identity(object()) is None


_SEED_SCRIPT = (
    "from abicheck.compatibility_evaluation_frontend import "
    "builtin_policy_identity as p, severity_preset_identity as s\n"
    "from abicheck.checker_policy import VALID_BASE_POLICIES\n"
    "from abicheck.policy.severity import SEVERITY_PRESETS\n"
    "print(sorted((n, p(n).sha256) for n in VALID_BASE_POLICIES))\n"
    "print(sorted((n, s(n).sha256) for n in SEVERITY_PRESETS))\n"
)


def _digests_under_seed(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(
        [sys.executable, "-c", _SEED_SCRIPT],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return proc.stdout


@pytest.fixture(scope="module")
def builtin_digests() -> dict[str, str]:
    return {seed: _digests_under_seed(seed) for seed in ("0", "1", "4242")}


def test_cell_builtin_policy_identity(builtin_digests: dict[str, str]) -> None:
    """A digest over code-defined kind *sets*: it must not follow the
    per-process set iteration order, and two bases must not share one."""
    from abicheck.checker_policy import VALID_BASE_POLICIES
    from abicheck.compatibility_evaluation_frontend import builtin_policy_identity

    assert len(set(builtin_digests.values())) == 1, builtin_digests
    digests = {builtin_policy_identity(n).sha256 for n in VALID_BASE_POLICIES}
    assert len(digests) == len(VALID_BASE_POLICIES)


def test_cell_severity_preset_identity(builtin_digests: dict[str, str]) -> None:
    from abicheck.compatibility_evaluation_frontend import severity_preset_identity

    assert len(set(builtin_digests.values())) == 1, builtin_digests
    assert severity_preset_identity("info-only") == severity_preset_identity(
        "info_only"
    )
    assert (
        severity_preset_identity("strict").sha256
        != severity_preset_identity("info-only").sha256
    )


# -- files on disk ------------------------------------------------------------


def _elf_header(machine: int) -> bytes:
    # e_ident (16) + e_type (2, ET_DYN) + e_machine (2), little-endian.
    return (
        b"\x7fELF\x02\x01\x01"
        + b"\x00" * 9
        + (3).to_bytes(2, "little")
        + (machine.to_bytes(2, "little"))
    )


def test_cell_read_elf_identity(tmp_path: Path) -> None:
    """The header bytes are the identity, never where the file lives: the
    same library under a checkout with a space or behind a symlinked root
    reads the same; another machine does not."""
    from abicheck.frontends.action.library_selection import read_elf_identity

    a = tmp_path / "checkout a" / "lib" / "libx.so"
    a.parent.mkdir(parents=True)
    a.write_bytes(_elf_header(62) + b"\x00" * 44)
    link = tmp_path / "link"
    link.symlink_to(a.parent.parent, target_is_directory=True)
    other = tmp_path / "liby.so"
    other.write_bytes(_elf_header(183) + b"\x00" * 44)
    ident = read_elf_identity(a)
    assert ident is not None
    assert read_elf_identity(link / "lib" / "libx.so") == ident
    assert read_elf_identity(a.parent / ".." / "lib" / "libx.so") == ident
    assert read_elf_identity(other) not in (None, ident)
    (tmp_path / "not_elf").write_bytes(b"MZ" + b"\x00" * 40)
    assert read_elf_identity(tmp_path / "not_elf") is None


def test_cell_recorded_rules(tmp_path: Path) -> None:
    """Manifest class ``config.location_dependent_fingerprint`` (#1492): the
    ownership rules a side records are a function of its header tree's
    *relative* layout, never of where the tree sits. The same layout under
    the project, nested deeper, outside it, behind a symlinked root or under
    a directory with a space records one rule set; a different layout does
    not (negative control)."""
    from types import SimpleNamespace

    from abicheck.extract.ownership_stamp import recorded_rules
    from abicheck.workflows.ownership_request import (
        ownership_request_from_config,
        with_target_roots,
    )

    project = tmp_path / "project"
    project.mkdir()
    config = SimpleNamespace(ownership=None, public_header_dirs=())
    layout = ("include", "include/oneapi/ccl")

    def record(base: Path, rels: tuple[str, ...] = layout) -> object:
        roots = [base / r for r in rels]
        for r in roots:
            r.mkdir(parents=True, exist_ok=True)
        request = ownership_request_from_config(config, project)
        return recorded_rules(with_target_roots(request, roots, ()))

    reference = record(project / "inst-2021.14")
    link = tmp_path / "link"
    link.symlink_to(project / "inst-2021.14", target_is_directory=True)
    spellings = {
        "sibling release": project / "inst-2021.15",
        "nested deeper": project / "nested" / "deeper" / "v2",
        "outside the project": tmp_path / "outside" / "release",
        "space in path": tmp_path / "my checkout" / "release b",
        "symlinked root": link,
    }
    for name, base in spellings.items():
        assert record(base) == reference, name
    assert record(project / "flat", ("include",)) != reference


def test_cell_build_side_identity(tmp_path: Path) -> None:
    """A release side's acquisition key names *which* files are parsed, so
    it is spelling-invariant over one tree (``./``, ``..``, a symlinked
    root) and differs for a different header."""
    from abicheck.workflows.release_public_surface import build_side_identity

    root = tmp_path / "checkout a"
    (root / "include").mkdir(parents=True)
    (root / "include" / "api.h").write_text("int f(void);\n")
    (root / "include" / "other.h").write_text("int g(void);\n")
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)

    def key(header: Path, include: Path):
        return build_side_identity(
            [header],
            [include],
            lang="c",
            exclude_headers=(),
            public_header_dirs=None,
            compile_context=None,
            depth=None,
            include_dependencies=False,
        )

    base = key(root / "include" / "api.h", root / "include")
    assert key(root / "include" / "." / "api.h", root / "include") == base
    assert key(root / "include" / ".." / "include" / "api.h", root / "include") == base
    assert key(link / "include" / "api.h", link / "include") == base
    assert key(root / "include" / "other.h", root / "include") != base


def test_cell_stored_capture_identity(tmp_path: Path) -> None:
    """A stored package's recorded library identity travels with the
    package, not with the directory it was unpacked into."""
    from abicheck.bundle import stored_capture_identity
    from abicheck.project_snapshot_store import (
        DirectoryObjectStore,
        write_project_manifest,
    )
    from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
    from abicheck.storage.import_bundle_facts import (
        BUNDLE_FACTS_ARTIFACT_TYPE,
        import_bundle_facts,
    )
    from abicheck.workflows.release_package import resolve_release_package_map
    from tests.test_cli_compare_release_evidence_preservation import (
        _fn,
        _snap,
    )

    def build(root: Path, filename: str) -> tuple:
        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                "liba.so": snapshot_to_dict(
                    _snap("liba.so", "1.0", [_fn("foo", "_Z3foov")])
                ),
            },
            "filesystem_aliases": {"liba.so": ["liba.so.1"]},
            "library_filenames": {"liba.so": filename},
            "manifest": None,
        }
        pkg = root / "pkg"
        manifest = import_bundle_facts(
            doc,
            store=DirectoryObjectStore(pkg),
            max_known_schema_version=SCHEMA_VERSION,
            variant_id="v1",
        )
        write_project_manifest(pkg, manifest)
        (sub,) = resolve_release_package_map(
            pkg, variant_id=None, dest_root=root / "resolved"
        ).values()
        name, aliases, _ = stored_capture_identity(sub)
        return name, aliases

    base = build(tmp_path / "a", "liba.so.1.2.3")
    assert base == ("liba.so.1.2.3", ("liba.so.1",))
    assert build(tmp_path / "deeper" / "checkout b", "liba.so.1.2.3") == base
    assert build(tmp_path / "c", "liba.so.1.2.4") != base


# -- report and template-surface keys ----------------------------------------


def test_cell_resolve_cross_abi_identity() -> None:
    """Scheme-independent by contract: an Itanium, a Mach-O-decorated
    Itanium, and an MSVC spelling of ``lib::add`` are one declaration;
    ``lib::sub`` is another."""
    from abicheck.workflows.aggregate.reconcile import resolve_cross_abi_identity

    def ident(symbol: str):
        result = resolve_cross_abi_identity(
            {"kind": "func_removed", "symbol": symbol, "description": "removed"}
        )
        assert result is not None, symbol
        return result.primary_id

    base = ident("_ZN3lib3addEii")
    assert ident("__ZN3lib3addEii") == base
    assert ident("?add@lib@@YAHHH@Z") == base
    assert ident("_ZN3lib3subEii") != base


def _decl(name: str, mangled: str):
    from abicheck.model import Function

    return Function(name=name, mangled=mangled, return_type="int")


def test_cell_alias_identity() -> None:
    """The ambiguity-safe tier keeps the namespace and drops what lives only
    in the mangling (signature, ABI tag)."""
    from abicheck.compare.template_surface import alias_identity

    base = alias_identity(_decl("lib::foo", "_ZN3lib3fooEPc"))
    assert alias_identity(_decl("lib::foo", "_ZN3lib3fooEPDu")) == base
    assert alias_identity(_decl("lib::foo", "_ZN3lib3fooB3tagEPc")) == base
    assert alias_identity(_decl("other::foo", "_ZN5other3fooEPc")) != base


def test_cell_cpo_identity() -> None:
    """One customization point keys alike as a function and as the variable
    it became; another namespace's ``foo`` does not."""
    from abicheck.compare.template_surface import cpo_identity
    from abicheck.model import Variable

    def stem(qname: str) -> str:
        return qname.partition("(")[0]

    fn = cpo_identity(_decl("ns1::foo", "_ZN3ns13fooEv"), function_stem=stem)
    var = cpo_identity(
        Variable(name="ns1::foo", mangled="_ZN3ns13fooE", type="int"),
        function_stem=stem,
    )
    assert fn == var
    other = cpo_identity(_decl("ns2::foo", "_ZN3ns23fooEv"), function_stem=stem)
    assert other != fn


def test_registry_names_real_functions() -> None:
    import importlib

    for qual in cat.ENVIRONMENT_CELLS:
        module, func = qual.split(":")
        assert callable(getattr(importlib.import_module(module), func)), qual
