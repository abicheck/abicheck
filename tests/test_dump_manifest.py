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
"""ADR-050 D3 (G32 Phase B) — the ``--dump-manifest`` YAML parser.

Scope: this module tests abicheck.dump_manifest as a pure parser (text/path
in, DumpManifest out or ManifestValidationError raised). Nothing here
invokes castxml/clang or exercises the real dump loop -- that's
dumper_manifest.py's own test module, once it exists.
"""

from __future__ import annotations

import pytest

from abicheck.dump_manifest import (
    DumpManifest,
    IncludeEntry,
    TranslationUnit,
    load_manifest,
    parse_manifest,
    single_tu_manifest,
)
from abicheck.errors import ManifestValidationError

_MINIMAL = """\
roots:
  - include/foo.h
translation_units:
  - name: main
    forced_includes:
      - include/foo.h
"""


def test_minimal_manifest_round_trips(tmp_path):
    manifest = parse_manifest(_MINIMAL, base_dir=tmp_path)
    assert manifest.compiler == "c++"
    assert manifest.target is None
    assert manifest.frontend_context == "host"
    assert manifest.public_header_paths == ()
    assert manifest.public_header_dirs == ()
    assert manifest.roots == (tmp_path / "include" / "foo.h",)
    assert len(manifest.translation_units) == 1
    tu = manifest.translation_units[0]
    assert tu.name == "main"
    assert tu.forced_includes == (tmp_path / "include" / "foo.h",)
    assert tu.includes == ()
    assert tu.required is True
    assert tu.contributes_to_abi is True


def test_full_manifest_parses_every_field(tmp_path):
    text = """\
compiler: clang++
target: x86_64-linux-gnu
frontend_context: host
public_header_paths:
  - include/foo.h
public_header_dirs:
  - include
roots:
  - include/foo.h
  - include/bar.h
translation_units:
  - name: main
    forced_includes:
      - include/foo.h
    includes:
      - vendor/include
      - path: ../sibling
        project_owned: true
    required: true
    contributes_to_abi: true
  - name: optional-extra
    forced_includes:
      - include/bar.h
    required: false
    contributes_to_abi: false
"""
    manifest = parse_manifest(text, base_dir=tmp_path)
    assert manifest.compiler == "clang++"
    assert manifest.target == "x86_64-linux-gnu"
    assert manifest.public_header_paths == (tmp_path / "include" / "foo.h",)
    assert manifest.public_header_dirs == (tmp_path / "include",)
    assert manifest.roots == (tmp_path / "include" / "foo.h", tmp_path / "include" / "bar.h")
    assert len(manifest.translation_units) == 2
    main_tu, extra_tu = manifest.translation_units
    assert main_tu.includes == (
        IncludeEntry(path=tmp_path / "vendor" / "include"),
        IncludeEntry(path=tmp_path / ".." / "sibling", project_owned=True),
    )
    assert extra_tu.required is False
    assert extra_tu.contributes_to_abi is False


def test_absolute_paths_pass_through_unchanged(tmp_path):
    header = tmp_path / "elsewhere" / "foo.h"
    text = f"""\
roots:
  - {header}
translation_units:
  - name: main
    forced_includes:
      - {header}
"""
    manifest = parse_manifest(text, base_dir=tmp_path / "manifest_dir")
    assert manifest.roots == (header,)


def test_missing_roots_rejected(tmp_path):
    text = "translation_units:\n  - name: main\n"
    with pytest.raises(ManifestValidationError, match="roots"):
        parse_manifest(text, base_dir=tmp_path)


def test_empty_roots_rejected(tmp_path):
    text = "roots: []\ntranslation_units:\n  - name: main\n"
    with pytest.raises(ManifestValidationError, match="non-empty"):
        parse_manifest(text, base_dir=tmp_path)


def test_missing_translation_units_rejected(tmp_path):
    text = "roots:\n  - foo.h\n"
    with pytest.raises(ManifestValidationError, match="translation_units"):
        parse_manifest(text, base_dir=tmp_path)


def test_empty_translation_units_rejected(tmp_path):
    text = "roots:\n  - foo.h\ntranslation_units: []\n"
    with pytest.raises(ManifestValidationError, match="non-empty"):
        parse_manifest(text, base_dir=tmp_path)


def test_unknown_top_level_field_rejected(tmp_path):
    text = _MINIMAL + "bogus_field: 1\n"
    with pytest.raises(ManifestValidationError, match="bogus_field"):
        parse_manifest(text, base_dir=tmp_path)


def test_unknown_tu_field_rejected(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    forced_includes:
      - foo.h
    bogus_field: 1
"""
    with pytest.raises(ManifestValidationError, match="bogus_field"):
        parse_manifest(text, base_dir=tmp_path)


def test_unknown_include_mapping_field_rejected(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    includes:
      - path: vendor
        bogus_field: 1
"""
    with pytest.raises(ManifestValidationError, match="bogus_field"):
        parse_manifest(text, base_dir=tmp_path)


def test_duplicate_top_level_key_rejected(tmp_path):
    text = "roots:\n  - foo.h\nroots:\n  - bar.h\ntranslation_units:\n  - name: main\n"
    with pytest.raises(ManifestValidationError, match="duplicate key 'roots'"):
        parse_manifest(text, base_dir=tmp_path)


def test_duplicate_nested_key_rejected(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    name: other
"""
    with pytest.raises(ManifestValidationError, match="duplicate key 'name'"):
        parse_manifest(text, base_dir=tmp_path)


def test_duplicate_tu_name_rejected(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    forced_includes:
      - foo.h
  - name: main
    forced_includes:
      - foo.h
"""
    with pytest.raises(ManifestValidationError, match="duplicate translation unit name 'main'"):
        parse_manifest(text, base_dir=tmp_path)


def test_contributes_to_abi_requires_required(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    forced_includes:
      - foo.h
    required: false
    contributes_to_abi: true
"""
    with pytest.raises(ManifestValidationError, match="contributes_to_abi.*requires.*required"):
        parse_manifest(text, base_dir=tmp_path)


def test_contributes_to_abi_false_with_required_false_is_allowed(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    forced_includes:
      - foo.h
    required: false
    contributes_to_abi: false
"""
    manifest = parse_manifest(text, base_dir=tmp_path)
    tu = manifest.translation_units[0]
    assert tu.required is False
    assert tu.contributes_to_abi is False


def test_frontend_context_host_is_accepted(tmp_path):
    text = _MINIMAL + "frontend_context: host\n"
    manifest = parse_manifest(text, base_dir=tmp_path)
    assert manifest.frontend_context == "host"


def test_frontend_context_device_rejected_as_not_yet_implemented(tmp_path):
    text = _MINIMAL + "frontend_context: device\n"
    with pytest.raises(ManifestValidationError, match="Phase D"):
        parse_manifest(text, base_dir=tmp_path)


def test_include_entry_bare_string_form(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    includes:
      - vendor/include
"""
    manifest = parse_manifest(text, base_dir=tmp_path)
    assert manifest.translation_units[0].includes == (
        IncludeEntry(path=tmp_path / "vendor" / "include"),
    )


def test_include_entry_mapping_form_requires_path(tmp_path):
    text = """\
roots:
  - foo.h
translation_units:
  - name: main
    includes:
      - project_owned: true
"""
    with pytest.raises(ManifestValidationError, match="requires a non-empty 'path'"):
        parse_manifest(text, base_dir=tmp_path)


def test_malformed_document_not_a_mapping_rejected(tmp_path):
    with pytest.raises(ManifestValidationError, match="must be a YAML mapping"):
        parse_manifest("- just\n- a\n- list\n", base_dir=tmp_path)


def test_invalid_yaml_syntax_rejected(tmp_path):
    with pytest.raises(ManifestValidationError, match="invalid YAML"):
        parse_manifest("roots: [unterminated\n", base_dir=tmp_path)


def test_tu_missing_name_rejected(tmp_path):
    text = "roots:\n  - foo.h\ntranslation_units:\n  - forced_includes:\n      - foo.h\n"
    with pytest.raises(ManifestValidationError, match="missing required field 'name'"):
        parse_manifest(text, base_dir=tmp_path)


def test_load_manifest_reads_file_and_resolves_relative_to_its_own_dir(tmp_path):
    manifest_dir = tmp_path / "project"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text(_MINIMAL)
    manifest = load_manifest(manifest_dir / "manifest.yaml")
    assert manifest.base_dir == manifest_dir
    assert manifest.roots == (manifest_dir / "include" / "foo.h",)


def test_single_tu_manifest_synthesizes_legacy_equivalent(tmp_path):
    headers = [tmp_path / "foo.h", tmp_path / "bar.h"]
    includes = [tmp_path / "vendor"]
    manifest = single_tu_manifest(headers, includes, base_dir=tmp_path)
    assert isinstance(manifest, DumpManifest)
    assert manifest.roots == tuple(headers)
    assert len(manifest.translation_units) == 1
    tu = manifest.translation_units[0]
    assert isinstance(tu, TranslationUnit)
    assert tu.name == "legacy-main"
    assert tu.forced_includes == tuple(headers)
    assert tu.includes == (IncludeEntry(path=tmp_path / "vendor"),)
    assert tu.required is True
    assert tu.contributes_to_abi is True


def test_target_must_be_string_or_null(tmp_path):
    text = _MINIMAL + "target: 123\n"
    with pytest.raises(ManifestValidationError, match="'target' must be a string or null"):
        parse_manifest(text, base_dir=tmp_path)
