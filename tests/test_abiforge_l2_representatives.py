# SPDX-License-Identifier: Apache-2.0
"""Run the generated L2 witnesses against both public snapshot frontends."""

import json
import platform
import shutil
import sys

import pytest

from abiforge.run.l2_representatives import FAMILIES, run


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine() != "x86_64",
    reason="SysV AMD64 register and Itanium vtable experiments",
)
@pytest.mark.parametrize("frontend", ["clang", "castxml"])
def test_generated_representatives(tmp_path, frontend):
    if not shutil.which("clang++") or not shutil.which(frontend):
        pytest.skip(f"requires clang++ and {frontend}")
    run(tmp_path, "clang++", frontend)
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert set(receipt) == {
        f"{family}/{variant}"
        for family, variants in FAMILIES.items()
        for variant in ("old", *variants)
    }
    assert receipt["callback_return/change"]["verdict"] == "BREAKING"


def test_verdict_gate_works_without_diagnostic_attribution():
    from abiforge.run.l2_representatives import check_report

    assert check_report(
        "callback_value", "change", {"verdict": "BREAKING"}, 4, True
    ) == {"verdict": "BREAKING"}
    for verdict, code in (
        ("COMPATIBLE_WITH_RISK", 0),
        ("BREAKING", 1),
        ("NO_CHANGE", 0),
    ):
        with pytest.raises(ValueError):
            check_report("callback_value", "change", {"verdict": verdict}, code, True)
    with pytest.raises(ValueError):
        check_report("callback_reference", "change", {"verdict": "BREAKING"}, 4, True)


def test_callback_alias_scope_and_indirect_controls():
    from types import SimpleNamespace

    from abicheck.model.callback_type_index import CallbackTypeIndex

    records = [
        SimpleNamespace(name="V", qualified_name=name, trivial_for_calls=True)
        for name in ("V", "ns::V")
    ]
    declarations = SimpleNamespace(
        types=records,
        typedefs_qualified={
            "ns::Visit": "long (*)(const V, ::V, V*, const V&)",
            "api::Alias": "ns::Visit",
            "Cycle": "Cycle",
        },
    )
    types = CallbackTypeIndex(SimpleNamespace(declarations=declarations))
    slots = types.arguments("Alias", "api")
    assert slots[0] == "ns::V" and slots[1] == "V"
    assert slots[2] not in types.traits and slots[3] not in types.traits
    assert types.arguments("Cycle", "") == {}


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="glibc ELF binding trace")
def test_duplicate_export_is_checked_per_loaded_provider(tmp_path):
    import subprocess

    from abiforge.run.duplicate_provider import run as replay

    if not shutil.which("cc") or not shutil.which("patchelf"):
        pytest.skip("requires cc and patchelf")
    if (
        "--rename-dynamic-symbols"
        not in subprocess.run(
            ["patchelf", "--help"], capture_output=True, text=True
        ).stderr
    ):
        pytest.skip("patchelf lacks symbol renaming")
    libraries, clients = [], []
    for target, value in (("cpu", 17), ("dpc", 29)):
        library = tmp_path / f"lib{target}.so"
        client = tmp_path / f"client_{target}"
        src = tmp_path / f"{target}.c"
        src.write_text(f"int shared(void) {{ return {value}; }}")
        subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                src,
                f"-Wl,-soname,{library.name}",
                "-o",
                library,
            ],
            check=True,
        )
        src.write_text(
            f"int shared(void); int main(void) {{ return shared() != {value}; }}"
        )
        subprocess.run(["cc", src, library, "-o", client], check=True)
        libraries.append(library)
        clients.append(client)
    dest = tmp_path / "result"
    replay(*libraries, *clients, "shared", dest)
    receipt = json.loads((dest / "receipt.json").read_text())
    assert receipt["cases"]["withdrawal"]["libcpu.so"]["exit"] == 127
    assert receipt["cases"]["withdrawal"]["libdpc.so"]["exit"] == 0


def test_nested_callback_record_needs_an_unambiguous_direct_field():
    from types import SimpleNamespace

    from abicheck.model.callback_type_index import CallbackTypeIndex
    from abicheck.model.fact import Fact

    record = SimpleNamespace(
        name="Ops", qualified_name="api::Ops", trivial_for_calls=True
    )
    declarations = SimpleNamespace(
        types=[record], typedefs_qualified={"api::OpsPtr": "const Ops*"}
    )
    index = CallbackTypeIndex(SimpleNamespace(declarations=declarations))

    def identify(spelling, fact):
        return index._direct_record(
            spelling, fact, "api::Interface", allow_spelling=True
        )

    assert identify("const Ops *", Fact.not_collected()) is record
    assert identify("OpsPtr", Fact.present(("api::Ops",))) is record
    assert identify("OpsPtr *", Fact.not_collected()) is None
    assert identify("Ops **", Fact.not_collected()) is None
    assert identify("Ops *", Fact.present(("other::Ops",))) is None
    assert identify("Ops *", Fact.present(())) is None
