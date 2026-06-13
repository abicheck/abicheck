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

"""Unit tests for declaration provenance (ADR-015, schema v6)."""

from __future__ import annotations

import pytest

from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    ScopeOrigin,
    Variable,
)
from abicheck.provenance import (
    apply_provenance,
    build_public_set,
    classify_origin,
    header_from_location,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

# ── header_from_location ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "loc,expected",
    [
        ("include/api.h:42", "include/api.h"),
        ("include/api.h:42:9", "include/api.h"),
        ("/build/src/foo.hpp:1", "/build/src/foo.hpp"),
        ("plain.h", "plain.h"),
        ("C:\\proj\\inc\\api.h:10", "C:\\proj\\inc\\api.h"),  # drive letter colon kept
        (None, None),
        ("", None),
    ],
)
def test_header_from_location(loc, expected):
    assert header_from_location(loc) == expected


# ── classify_origin ───────────────────────────────────────────────────────────


def _classify(header, public_headers=None, public_dirs=None):
    hs, ds, have = build_public_set(public_headers, public_dirs)
    return classify_origin(header, hs, ds, have_public_set=have)


def test_no_public_set_is_always_unknown():
    # Decision D4: without a public set, everything is UNKNOWN regardless of path.
    assert _classify("/usr/include/stdio.h") is ScopeOrigin.UNKNOWN
    assert _classify("include/api.h") is ScopeOrigin.UNKNOWN


def test_none_header_is_unknown_even_with_public_set():
    assert _classify(None, public_headers=["include/api.h"]) is ScopeOrigin.UNKNOWN


def test_exact_public_header_suffix_match_through_build_prefix():
    # Build path carries an absolute prefix the user never typed.
    origin = _classify(
        "/build/abc123/src/include/api.h",
        public_headers=["include/api.h"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


def test_basename_fallback_match():
    origin = _classify(
        "/wherever/it/landed/api.h",
        public_headers=["api.h"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


def test_public_header_dir_containment():
    origin = _classify(
        "/build/proj/include/sub/widget.h",
        public_dirs=["include"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


def test_system_header_classified_when_set_present():
    origin = _classify("/usr/include/stdio.h", public_headers=["include/api.h"])
    assert origin is ScopeOrigin.SYSTEM_HEADER


def test_system_header_with_sysroot_prefix():
    origin = _classify(
        "/opt/sysroot/usr/include/bits/types.h",
        public_headers=["include/api.h"],
    )
    assert origin is ScopeOrigin.SYSTEM_HEADER


def test_private_header_when_not_public_and_not_system():
    origin = _classify(
        "/build/proj/src/internal/impl.h",
        public_headers=["include/api.h"],
        public_dirs=["include"],
    )
    assert origin is ScopeOrigin.PRIVATE_HEADER


def test_public_takes_precedence_over_system_path():
    # A header that both matches the public set and lives under usr/include
    # should classify PUBLIC (public check runs first).
    origin = _classify(
        "/usr/include/mylib/api.h",
        public_dirs=["mylib"],
    )
    assert origin is ScopeOrigin.PUBLIC_HEADER


@pytest.mark.parametrize(
    "header",
    [
        "/build/proj/generated/messages.h",
        "/build/proj/src/moc_widget.cpp",
        "/build/proj/proto/service.pb.h",
        "/build/proj/schema_generated.h",
        "/build/proj/api.grpc.pb.h",
    ],
)
def test_generated_headers_classified(header):
    # A public set must be present (opt-in), but the generated path is neither
    # public nor system → GENERATED.
    origin = _classify(header, public_headers=["include/api.h"])
    assert origin is ScopeOrigin.GENERATED


def test_export_only_when_no_header_but_symbol_exported():
    hs, ds, have = build_public_set(["include/api.h"], None)
    origin = classify_origin(None, hs, ds, have_public_set=have, export_only=True)
    assert origin is ScopeOrigin.EXPORT_ONLY


def test_export_only_ignored_without_public_set():
    # D4: no public set → UNKNOWN regardless of export-only linkage.
    hs, ds, have = build_public_set(None, None)
    origin = classify_origin(None, hs, ds, have_public_set=have, export_only=True)
    assert origin is ScopeOrigin.UNKNOWN


def test_no_header_not_exported_is_unknown():
    hs, ds, have = build_public_set(["include/api.h"], None)
    origin = classify_origin(None, hs, ds, have_public_set=have, export_only=False)
    assert origin is ScopeOrigin.UNKNOWN


# ── apply_provenance ──────────────────────────────────────────────────────────


def _snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="pub",
                mangled="pub",
                return_type="void",
                source_location="/build/include/api.h:10",
            ),
            Function(
                name="priv",
                mangled="priv",
                return_type="void",
                source_location="/build/src/impl.h:20",
            ),
            Function(name="noloc", mangled="noloc", return_type="void"),
        ],
        variables=[
            Variable(
                name="g",
                mangled="g",
                type="int",
                source_location="/build/include/api.h:5",
            ),
        ],
        types=[
            RecordType(
                name="S", kind="struct", source_location="/build/include/api.h:30"
            ),
        ],
        enums=[
            EnumType(
                name="E",
                members=[EnumMember(name="A", value=0)],
                source_location="/build/include/api.h:40",
            ),
        ],
    )


def test_apply_provenance_opt_in_classification():
    snap = apply_provenance(_snapshot(), public_headers=["include/api.h"])
    by_name = {f.name: f for f in snap.functions}
    assert by_name["pub"].source_header == "/build/include/api.h"
    assert by_name["pub"].origin is ScopeOrigin.PUBLIC_HEADER
    assert by_name["priv"].origin is ScopeOrigin.PRIVATE_HEADER
    # No source location → no header, UNKNOWN origin.
    assert by_name["noloc"].source_header is None
    assert by_name["noloc"].origin is ScopeOrigin.UNKNOWN
    assert snap.variables[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert snap.types[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert snap.enums[0].origin is ScopeOrigin.PUBLIC_HEADER


def test_apply_provenance_no_set_keeps_unknown_but_fills_header():
    # source_header is descriptive metadata and is always populated; origin
    # stays UNKNOWN without a public set (decision D4).
    snap = apply_provenance(_snapshot())
    assert snap.functions[0].source_header == "/build/include/api.h"
    assert snap.functions[0].origin is ScopeOrigin.UNKNOWN
    assert snap.types[0].origin is ScopeOrigin.UNKNOWN


# ── serialization round-trip (schema v6) ──────────────────────────────────────


def test_serialization_round_trip_preserves_provenance():
    snap = apply_provenance(_snapshot(), public_headers=["include/api.h"])
    d = snapshot_to_dict(snap)
    assert d["schema_version"] == 8
    # Enum value serialized as a plain string.
    assert d["functions"][0]["origin"] == "public_header"
    assert d["functions"][0]["source_header"] == "/build/include/api.h"

    back = snapshot_from_dict(d)
    assert back.functions[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert back.functions[0].source_header == "/build/include/api.h"
    assert back.enums[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert back.enums[0].source_header == "/build/include/api.h"
    assert back.types[0].origin is ScopeOrigin.PUBLIC_HEADER
    assert back.variables[0].origin is ScopeOrigin.PUBLIC_HEADER


def test_old_snapshot_without_provenance_loads_as_unknown():
    # A pre-v6 snapshot dict has no source_header / origin keys.
    legacy = {
        "library": "libold.so",
        "version": "1.0",
        "functions": [{"name": "f", "mangled": "f", "return_type": "void"}],
        "variables": [{"name": "v", "mangled": "v", "type": "int"}],
        "types": [{"name": "T", "kind": "struct"}],
        "enums": [{"name": "E", "members": []}],
    }
    snap = snapshot_from_dict(legacy)
    assert snap.functions[0].origin is ScopeOrigin.UNKNOWN
    assert snap.functions[0].source_header is None
    assert snap.variables[0].origin is ScopeOrigin.UNKNOWN
    assert snap.types[0].origin is ScopeOrigin.UNKNOWN
    assert snap.enums[0].origin is ScopeOrigin.UNKNOWN


# ── castxml dumper wires source_location onto records/variables/enums ─────────
# (regression guard for the dumper fix; uses synthetic XML, no castxml binary)


def _castxml_root():
    from xml.etree.ElementTree import Element, SubElement

    root = Element("CastXML")
    f = SubElement(root, "File")
    f.set("id", "f1")
    f.set("name", "/build/inc/api.h")
    # Direct file/line form on a struct.
    s = SubElement(root, "Struct")
    s.set("id", "_s")
    s.set("name", "Widget")
    s.set("size", "64")
    s.set("align", "32")
    s.set("file", "f1")
    s.set("line", "12")
    # Location-ref form on a variable.
    loc = SubElement(root, "Location")
    loc.set("id", "l1")
    loc.set("file", "f1")
    loc.set("line", "20")
    fund = SubElement(root, "FundamentalType")
    fund.set("id", "_int")
    fund.set("name", "int")
    v = SubElement(root, "Variable")
    v.set("id", "_v")
    v.set("name", "g_count")
    v.set("mangled", "g_count")
    v.set("type", "_int")
    v.set("location", "l1")
    # Enumeration with direct file/line.
    e = SubElement(root, "Enumeration")
    e.set("id", "_e")
    e.set("name", "Color")
    e.set("file", "f1")
    e.set("line", "30")
    return root


def test_castxml_populates_source_location_on_types_vars_enums():
    from abicheck.dumper import _CastxmlParser

    root = _castxml_root()
    parser = _CastxmlParser(
        root, exported_dynamic={"g_count"}, exported_static={"g_count"}
    )
    rec = next(t for t in parser.parse_types() if t.name == "Widget")
    assert rec.source_location == "/build/inc/api.h:12"
    var = next(v for v in parser.parse_variables() if v.name == "g_count")
    assert var.source_location == "/build/inc/api.h:20"
    enum = next(e for e in parser.parse_enums() if e.name == "Color")
    assert enum.source_location == "/build/inc/api.h:30"
