# SPDX-License-Identifier: Apache-2.0
"""ElfSymbol/ElfImport persistence and std-instantiation origin attribution.

Two defects, each tested as its class rather than its one reported input:

* ``origin_lib`` was written to a stored snapshot but never read back, so a
  cache hit (or any saved-then-loaded snapshot) lost every
  ``symbol_leaked_from_dependency_*`` finding. The invariant is that *every*
  field of the persisted symbol dataclasses survives a round trip -- derived
  from ``dataclasses.fields`` so a field added later is covered without
  editing this test.
* ``std::_Sp_counted_deleter<dnnl_stream*, ...>`` was attributed to
  libstdc++ although the runtime never saw ``dnnl_stream``. The oracle for
  the generated-binary test is ``c++filt``, independent of the mangled-name
  scanner under test.
"""

from __future__ import annotations

import dataclasses
import enum
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.elf_metadata import _guess_symbol_origin
from abicheck.extract.mangled_foreign_template_args import (
    has_foreign_template_argument,
)
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfImport, ElfMetadata, ElfSymbol
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict


def _non_default(cls: type, f: dataclasses.Field) -> object:
    """A value for *f* that differs from its default, chosen by type alone."""
    default = f.default
    if isinstance(default, enum.Enum):
        members = [m for m in type(default) if m is not default]
        return members[0]
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        return default + 7
    # str, Optional[str], or no default (the required ``name``).
    return f"{cls.__name__}-{f.name}-value"


@pytest.mark.parametrize("cls", [ElfSymbol, ElfImport])
def test_every_symbol_field_survives_snapshot_round_trip(cls: type) -> None:
    kwargs = {f.name: _non_default(cls, f) for f in dataclasses.fields(cls)}
    obj = cls(**kwargs)
    elf = ElfMetadata(soname="libx.so.1")
    if cls is ElfSymbol:
        elf.symbols = [obj]
    else:
        elf.imports = [obj]
    snap = AbiSnapshot(library="libx.so.1", version="1", elf=elf)
    loaded = snapshot_from_dict(snapshot_to_dict(snap))
    assert loaded.elf is not None
    got = (loaded.elf.symbols if cls is ElfSymbol else loaded.elf.imports)[0]
    lost = {
        f.name: (getattr(obj, f.name), getattr(got, f.name))
        for f in dataclasses.fields(cls)
        if getattr(got, f.name) != getattr(obj, f.name)
    }
    assert not lost, f"fields not round-tripped: {lost}"


def test_origin_lib_none_round_trips_as_none() -> None:
    elf = ElfMetadata(symbols=[ElfSymbol(name="f", origin_lib=None)])
    snap = AbiSnapshot(library="l", version="1", elf=elf)
    loaded = snapshot_from_dict(snapshot_to_dict(snap))
    assert loaded.elf is not None and loaded.elf.symbols[0].origin_lib is None


# Mangled spellings g++ 13 emits for the reported shape and its siblings.
_LOCAL_INSTANTIATIONS = [
    # std::_Sp_counted_deleter<dnnl_stream*, dnnl_status_t(*)(dnnl_stream*), ...>
    "_ZNSt19_Sp_counted_deleterIP11dnnl_streamPF13dnnl_status_tS1_ESaIvELN9__gnu_cxx12_Lock_policyE2EE10_M_disposeEv",
    "_ZNSt15_Sp_counted_ptrIP11dnnl_memoryLN9__gnu_cxx12_Lock_policyE2EE10_M_disposeEv",
    "_ZNSt23_Sp_counted_ptr_inplaceIN4dnnl4impl3fooESaIvELN9__gnu_cxx12_Lock_policyE2EE10_M_disposeEv",
    "_ZNSt12_Vector_baseIN3foo3BarESaIS1_EED2Ev",
    "_ZSt4sortIP11dnnl_memoryEvT_S2_",
    # vtable / typeinfo / typeinfo name of the same instantiation.
    "_ZTVSt19_Sp_counted_deleterIP11dnnl_streamPF13dnnl_status_tS1_ESaIvELN9__gnu_cxx12_Lock_policyE2EE",
    "_ZTISt19_Sp_counted_deleterIP11dnnl_streamPF13dnnl_status_tS1_ESaIvELN9__gnu_cxx12_Lock_policyE2EE",
    "_ZTSSt19_Sp_counted_deleterIP11dnnl_streamPF13dnnl_status_tS1_ESaIvELN9__gnu_cxx12_Lock_policyE2EE",
]
_RUNTIME_OWNED = [
    "_ZNSt6vectorIiSaIiEE9push_backERKi",
    "_ZNKSt5ctypeIcE8do_widenEc",
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEED1Ev",
    "_ZNSt8ios_base4InitC1Ev",
    "_ZTVSt9exception",
    "_ZTISt16_Sp_counted_baseILN9__gnu_cxx12_Lock_policyE2EE",
    "_ZTINSt6locale5facetE",
    # std templates over global C-library types libstdc++ owns.
    "_ZNKSt7codecvtIwc11__mbstate_tE6do_outERS0_PKwS4_RS4_PcS6_RS6_",
    "_ZNSt4fposI11__mbstate_tEC1Ev",
    "_ZSt4cout",
    "_ZNSt15_Sp_counted_ptrIPiLN9__gnu_cxx12_Lock_policyE2EE10_M_disposeEv",
]


@pytest.mark.parametrize("name", _LOCAL_INSTANTIATIONS)
def test_std_instantiation_over_own_type_is_native(name: str) -> None:
    assert has_foreign_template_argument(name)
    assert _guess_symbol_origin(name, ["libstdc++.so.6", "libc.so.6"]) is None


@pytest.mark.parametrize("name", _RUNTIME_OWNED)
def test_std_only_symbol_keeps_runtime_attribution(name: str) -> None:
    assert not has_foreign_template_argument(name)
    assert _guess_symbol_origin(name, ["libstdc++.so.6"]) == "libstdc++.so.6"


@pytest.mark.parametrize(
    "name",
    ["", "_Z", "_ZNSt", "_ZNSt99x", "_ZNSt3fooI", "_ZNSt3fooIP3barE", "foo"],
)
def test_malformed_or_truncated_name_is_never_reclassified(name: str) -> None:
    # Fail-closed: an unparsed name keeps whatever the prefix table says.
    assert not has_foreign_template_argument(name)


_CPP = """
#include <memory>
#include <vector>
#include <map>
#include <string>
#include <algorithm>
typedef enum { libown_ok } libown_status_t;
struct libown_stream {};
extern "C" libown_status_t libown_stream_destroy(libown_stream*);
namespace libown { struct Thing { int x; }; }
std::shared_ptr<libown_stream> a(libown_stream* s) { return {s, libown_stream_destroy}; }
std::shared_ptr<libown::Thing> b() { return std::make_shared<libown::Thing>(); }
std::vector<libown::Thing> c() { std::vector<libown::Thing> v; v.push_back({}); return v; }
void d(std::vector<libown::Thing*>& v) { std::sort(v.begin(), v.end()); }
std::shared_ptr<int> e() { return std::shared_ptr<int>(new int); }
std::vector<int> f() { std::vector<int> v; v.push_back(1); return v; }
std::map<std::string, int> g() { std::map<std::string, int> m; m["a"]; return m; }
"""


@pytest.mark.integration
@pytest.mark.skipif(
    not (shutil.which("g++") and shutil.which("nm") and shutil.which("c++filt")),
    reason="needs g++, nm and c++filt",
)
def test_generated_binary_against_cxxfilt_oracle(tmp_path: Path) -> None:
    src = tmp_path / "own.cpp"
    src.write_text(_CPP)
    lib = tmp_path / "libown.so"
    subprocess.run(
        ["g++", "-std=c++17", "-O1", "-fPIC", "-shared", str(src), "-o", str(lib)],
        check=True,
    )
    out = subprocess.run(
        ["nm", "-D", "--defined-only", str(lib)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    names = [
        ln.split()[-1]
        for ln in out.splitlines()
        if ln.split()
        and ln.split()[-1].startswith(
            ("_ZNSt", "_ZNKSt", "_ZSt", "_ZTVSt", "_ZTISt", "_ZTSSt", "_ZTVNSt")
        )
    ]
    demangled = subprocess.run(
        ["c++filt"], input="\n".join(names), capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert len(demangled) == len(names)
    assert any(n.startswith("_ZTVSt19_Sp_counted_deleter") for n in names)
    disagreements = [
        (plain, has_foreign_template_argument(mangled))
        for mangled, plain in zip(names, demangled, strict=True)
        if has_foreign_template_argument(mangled) != ("libown" in plain)
    ]
    # Exact agreement with the demangler: reclassified iff it names our type.
    assert not disagreements, disagreements
