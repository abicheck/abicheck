# SPDX-License-Identifier: Apache-2.0
"""Real native dump -> stored snapshots -> compare, without a Clang fallback."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.errors import AstContextMissingError, SnapshotError, ValidationError
from abicheck.extract.header_ast_backend import (
    _is_hybrid_request,
    _resolve_effective_ast_backend,
)
from abicheck.extract.headers.gcc.discovery import find_plugin
from abicheck.serialization import load_snapshot

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_plugins(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    compiler = shutil.which(os.environ.get("ABICHECK_NATIVE_GCC", "g++"))
    if not compiler:
        pytest.skip("native GCC unavailable")
    sdk = os.environ.get("ABICHECK_GCC_PLUGIN_HEADERS")
    if not sdk:
        probe = subprocess.run(
            [compiler, "-print-file-name=plugin"],
            text=True,
            capture_output=True,
            check=True,
        )
        sdk = str(Path(probe.stdout.strip()) / "include")
    if not (Path(sdk) / "gcc-plugin.h").is_file():
        pytest.skip("matching GCC plugin-dev headers unavailable")
    output = tmp_path_factory.mktemp("native-plugins")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck.extract.headers.gcc.build",
            "--compiler",
            compiler,
            "--headers",
            sdk,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return compiler, output


def cli(
    tmp_path: Path, args: list[str], plugin: Path
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "ABICHECK_GCC_PLUGIN": str(plugin),
        "ABICHECK_AST_FRONTEND": "gcc",
    }
    blocked = tmp_path / "blocked-tools"
    blocked.mkdir(exist_ok=True)
    marker = tmp_path / "non-native-frontend-called"
    for name in ("clang", "clang++", "castxml"):
        executable = blocked / name
        executable.write_text(
            "#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexit 91\n"
        )
        executable.chmod(0o755)
    env["PATH"] = str(blocked) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            *args,
            "--config",
            str(tmp_path / ".abicheck.yml"),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


@pytest.mark.parametrize("context", ["device", "both"])
def test_native_gcc_never_becomes_clang_for_device(context: str) -> None:
    with pytest.raises(AstContextMissingError, match="Native GCC"):
        _resolve_effective_ast_backend("gcc", context)


def test_native_gcc_manifest_is_explicitly_unsupported() -> None:
    with pytest.raises(ValidationError, match="dump-manifest"):
        _is_hybrid_request("gcc", dump_manifest_given=True, frontend_context="host")


def test_explicit_missing_plugin_fails_before_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(tmp_path / "missing.so"))
    with pytest.raises(SnapshotError, match="ABICHECK_GCC_PLUGIN"):
        find_plugin("not-invoked", "c++")


@pytest.mark.native_gcc
@pytest.mark.parametrize("language", ["c", "c++"])
def test_real_native_dump_compare_roundtrip(
    native_plugins: tuple[str, Path], tmp_path: Path, language: str
) -> None:
    cc, plugin = native_plugins
    (tmp_path / ".abicheck.yml").write_text(
        f"compile:\n  frontend: gcc\n  lang: {language}\n  compiler: {cc}\n  options: [-std={'c11' if language == 'c' else 'c++20'}]\n"
    )
    for side, extra in [("old", ""), ("new", "double y;")]:
        header = tmp_path / f"{side}.h"
        header.write_text(f"struct S{{int x;{extra}}};\nint api(struct S*);\n")
        source = tmp_path / f"{side}.c"
        source.write_text(f'#include "{side}.h"\nint api(struct S*p){{return p->x;}}\n')
        lib = tmp_path / f"{side}.so"
        subprocess.run(
            [
                cc,
                "-x",
                language,
                "-shared",
                "-fPIC",
                "-g0",
                str(source),
                "-o",
                str(lib),
            ],
            check=True,
            capture_output=True,
        )
        result = cli(
            tmp_path,
            ["dump", str(lib), "-H", str(header), "-o", str(tmp_path / f"{side}.json")],
            plugin,
        )
        assert result.returncode == 0, result.stderr
        snap = load_snapshot(tmp_path / f"{side}.json")
        assert snap.ast_producer == "gcc-experimental"
        assert len(snap.declarations.functions) == 1
        assert snap.dump_provenance and snap.dump_provenance["extraction_limitations"]
        assert snap.dump_provenance["compiler"]["plugin_sha256"]
        assert snap.dump_provenance["compiler"]["headers"] == [str(header)]
        assert not (tmp_path / "non-native-frontend-called").exists()
    report = tmp_path / "report.json"
    result = cli(
        tmp_path,
        [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "-o",
            f"json={report}",
        ],
        plugin,
    )
    assert result.returncode == 4, result.stderr
    payload = json.loads(report.read_text())
    assert payload["verdict"] == "BREAKING"
    assert any(c["kind"] == "type_size_changed" for c in payload["changes"])
    assert payload["analysis_assurance"]["status"] == "partial"
    assert any(
        "extraction limitation" in n for n in payload["analysis_assurance"]["notes"]
    )
    result = cli(
        tmp_path,
        [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "old.json"),
            "-o",
            f"json={report}",
        ],
        plugin,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text())
    assert payload["verdict"] == "NO_CHANGE"
    assert payload["analysis_assurance"]["status"] == "partial"


@pytest.mark.native_gcc
def test_native_missing_or_invalid_plugin_is_no_snapshot(
    native_plugins: tuple[str, Path], tmp_path: Path
) -> None:
    cc, _ = native_plugins
    (tmp_path / ".abicheck.yml").write_text(
        f"compile:\n  frontend: gcc\n  lang: c++\n  compiler: {cc}\n"
    )
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n")
    fake = tmp_path / "invalid.so"
    fake.write_bytes(b"not a shared object")
    for plugin in [tmp_path / "missing.so", fake]:
        output = tmp_path / "snapshot.json"
        result = cli(tmp_path, ["dump", "-H", str(header), "-o", str(output)], plugin)
        assert result.returncode != 0
        assert not output.exists()
        assert "GCC" in result.stderr or "GCC" in result.stdout


def test_native_snapshot_cache_keys_plugin_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck.workflows.dump.cache.key import _dump_cache_extra_key

    if not shutil.which("g++"):
        pytest.skip("GCC unavailable for cache identity probe")
    plugin = tmp_path / "plugin.so"
    plugin.write_bytes(b"one")
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(plugin))
    old = _dump_cache_extra_key("elf", "gcc", None, None)
    plugin.write_bytes(b"two")
    new = _dump_cache_extra_key("elf", "gcc", None, None)
    assert old != new
    assert new == _dump_cache_extra_key("elf", "gcc", None, None)


@pytest.mark.native_gcc
@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        ("extern int value;", "extern double value;", "var_type_changed"),
        ("using V=int;", "using V=const int;", "typedef_base_changed"),
        (
            "inline constexpr int value=1;",
            "inline constexpr int value=2;",
            "constant_changed",
        ),
        ("struct S{S(int);};", "struct S{S(int)=delete;};", "func_deleted"),
        ("struct S{S(int);};", "struct S{explicit S(int);};", "ctor_explicit_added"),
        (
            "struct S{int x=1;};",
            "struct S{int x=2;};",
            "field_default_initializer_changed",
        ),
        (
            "struct S{virtual int a();virtual int b();};",
            "struct S{virtual int b();virtual int a();};",
            "type_vtable_changed",
        ),
        (
            "template<class T=int>struct Box{T x;};",
            "template<class T=double>struct Box{T x;};",
            "template_param_default_changed",
        ),
        (
            "template<class T=int>using Ptr=T*;",
            "template<class T=double>using Ptr=T*;",
            "template_param_default_changed",
        ),
        (
            "template<class T=int>void f(T);",
            "template<class T=double>void f(T);",
            "template_param_default_changed",
        ),
        (
            "template<class T>concept C=sizeof(T)>1;",
            "template<class T>concept C=sizeof(T)>2;",
            "concept_constraint_changed",
        ),
    ],
)
def test_native_contract_changes_reach_checker(
    native_plugins: tuple[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: str,
    after: str,
    kind: str,
) -> None:
    from abicheck.checker import compare
    from abicheck.extract.headers.backend_protocol import HeaderParseRequest
    from abicheck.extract.headers.gcc.backend import GccBackend
    from abicheck.model import AbiSnapshot

    compiler, plugins = native_plugins
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(plugins))
    snapshots = []
    for side, text in [("old", before), ("new", after)]:
        header = tmp_path / f"{side}.hpp"
        header.write_text(text)
        parser = GccBackend().parse(
            HeaderParseRequest(
                headers=[header],
                extra_includes=[],
                gcc_path=compiler,
                gcc_option_tokens=("-std=c++20",),
                lang="c++",
                no_binary_evidence=True,
            )
        )
        fields = parser.normalized_fields
        snapshots.append(
            AbiSnapshot(
                library="headers",
                version=side,
                from_headers=True,
                header_only=True,
                ast_producer="gcc-experimental",
                functions=list(fields.functions),
                variables=list(fields.variables),
                types=list(fields.types),
                enums=list(fields.enums),
                semantic_ir=fields.semantic_ir,
                typedefs=fields.typedefs,
                typedefs_qualified=fields.typedefs_qualified,
                typedef_entity_ids=fields.typedef_entity_ids,
                constants=fields.constants,
                constant_entity_ids=fields.constant_entity_ids,
                **fields.clang_ast_tables(),
            )
        )
    result = compare(*snapshots)
    assert kind in {c.kind.value for c in result.changes}
    assert not compare(snapshots[0], snapshots[0]).changes
    assert result.analysis_assurance.status == "partial"


@pytest.mark.parametrize("format", ["macho", "pe"])
def test_native_header_backend_rejects_other_binary_formats(format: str) -> None:
    from abicheck.extract.header_ast_backend import validate_native_gcc_format

    with pytest.raises(ValidationError, match="ELF only"):
        validate_native_gcc_format("gcc", format, True)
    validate_native_gcc_format("gcc", format, False)
    validate_native_gcc_format("clang", format, True)


def test_native_manifest_is_rejected_during_planning() -> None:
    from types import SimpleNamespace

    from abicheck.workflows.artifact.resolve import (
        reject_unsupported_header_frontend_evidence,
    )

    side = SimpleNamespace(dump_manifest=object(), sources=None)
    evidence = SimpleNamespace(compile=None)
    with pytest.raises(ValidationError, match="dump-manifest"):
        reject_unsupported_header_frontend_evidence(None, ((side, evidence),), "gcc")
