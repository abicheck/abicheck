# SPDX-License-Identifier: Apache-2.0
"""Generate small SysV AMD64 ABI experiments and check their independent witnesses."""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from abiforge.model.seeds import C_SEED, XA_SEED
from abiforge.operators import OP_BY_NAME

# Each family has one baseline. Variants change one ABI-relevant condition.
FAMILIES = {
    "callback_return": ("change",),
    "callback_large_return": ("change",),
    "callback_pointer_return": ("change",),
    "callback_table": ("control", "swap", "passing"),
    "callback_value": ("change",),
    "callback_reference": ("change",),
    "layout": ("control", "swap", "alignment"),
    "calls": ("control", "assignment", "nontrivial", "register_class"),
    "virtual": (
        "control",
        "insert",
        "swap",
        "primary_override",
        "override",
        "move_base",
    ),
}


def callback_return_sources(family, variant):
    attr = "[[clang::trivial_abi]]" if variant == "old" else ""
    result = "V*" if family == "callback_pointer_return" else "V"
    header = f'struct {attr} V {{ long value; ~V(); }}; using Make = {result} (*)(); extern "C" int dispatch(Make);'
    if family == "callback_large_return":
        header = header.replace("long value;", "long value, second, third;")
    if family == "callback_pointer_return":
        body = "V::~V() {} int dispatch(Make f) { return f()->value == 42 ? 0 : 1; }"
        client = "return dispatch(+[]() -> V* { static V v{42}; return &v; });"
    else:
        body = """
    #include <new>
    #include <cstring>
    V::~V() {}
    int dispatch(Make f) {
        alignas(V) unsigned char storage[sizeof(V)] = {};
        V* result = new (storage) V(f());
        long expected = 42;
        int status = std::memcmp(storage, &expected, sizeof(expected)) != 0;
        result->~V();
        return status;
    }
    """
        client = "return dispatch(+[]() -> V { return V{42}; });"
    return header, body, client


def sources(family, variant):
    if family in {
        "callback_return",
        "callback_pointer_return",
        "callback_large_return",
    }:
        return callback_return_sources(family, variant)
    if family == "callback_table":
        attr = "[[clang::trivial_abi]]" if variant == "passing" else ""
        fields = (
            "Slot second; First first;"
            if variant == "swap"
            else "First first; Slot second;"
        )
        header = f'struct {attr} V {{ long value; ~V(); }}; using Slot = int (*)(V); using First = int (*)(const V&); struct Table {{ {fields} }}; extern "C" const Table* slots();'
        values = "right, left" if variant == "swap" else "left, right"
        body = f"""V::~V() {{}}
static int left(const V&) {{ return 19; }}
static int right(V v) {{ return v.value == 42 ? 17 : 23; }}
const Table* slots() {{ static const Table table = {{ {values} }}; return &table; }}
static_assert(sizeof(Table) == 2 * sizeof(Slot), "two slots");"""
        if variant == "control":
            body = body.replace("return 19;", "return 18 + 1;")
        return header, body, "return slots()->second(V{42});"
    if family in {"callback_value", "callback_reference"}:
        attr = "[[clang::trivial_abi]]" if variant == "old" else ""
        param = "V" if family == "callback_value" else "const V&"
        header = f'struct {attr} V {{ long value; ~V(); }}; using Visit = long (*)({param}); extern "C" long dispatch(Visit);'
        body = "V::~V() {} long dispatch(Visit f) { return f(V{42}); }"
        client = f"return dispatch(+[]({param} x) -> long {{ return x.value == 42 ? 0 : 1; }});"
        return header, body, client
    if family == "layout":
        roles = C_SEED.roles
        if variant == "swap":
            roles = OP_BY_NAME["struct_swap_fields"].edit(roles)
        header = roles["struct.point"][0] + ' extern "C" int read_left(const point*);'
        if variant == "alignment":
            header = header.replace("struct point {", "struct alignas(8) point {")
        header += ' struct envelope { char prefix; point member; }; extern "C" int member_offset(const envelope*);'
        expression = "0 + p->x" if variant == "control" else "p->x"
        body = f"int read_left(const point* p) {{ return {expression}; }}"
        body += '\nstatic_assert(sizeof(point) == 8, "stable size");'
        body += "\nint member_offset(const envelope*) { return __builtin_offsetof(envelope, member); }"
        client = "point p = {17, 29}; return argc == 1 ? read_left(&p) : member_offset(nullptr);"
    elif family == "calls":
        roles = XA_SEED.roles
        if variant == "nontrivial":
            roles = OP_BY_NAME["by_value_struct_became_nontrivial"].edit(roles)
        header, body = roles["xa.tv"]
        scalar = "long" if variant == "register_class" else "double"
        header += f"\nstruct Lane {{ {scalar} first; long second; }}; Lane lanes();"
        body += '\nstatic_assert(sizeof(Lane) == 16, "two eightbytes");'
        body += "\nLane lanes() { return Lane{1, 2}; }"
        if variant == "assignment":
            header = header.replace(
                "int a;", "int a; Tv& operator=(const Tv& v) { a = v.a; return *this; }"
            )
        if variant == "control":
            body = body.replace("return t.a + 1;", "return 1 + t.a;")
        body += '\nstatic_assert(sizeof(Tv) == 4 && alignof(Tv) == 4, "value layout");'
        client = "Tv t = {17}; return tv_pointer(&t) + tv_reference(t);"
    else:
        methods = {
            "insert": "virtual int extra(); virtual int first(); virtual int second();",
            "swap": "virtual int second(); virtual int first();",
        }.get(variant, "virtual int first(); virtual int second();")
        bases = "Right, Left" if variant == "move_base" else "Left, Right"
        override = "int first();" if variant == "override" else ""
        if variant == "primary_override":
            override = "int pad();"
        header = f"""
struct Left {{ virtual int pad(); int pad_value; }};
struct Right {{ {methods} int marker; }};
struct Both : {bases} {{ virtual int anchor(); {override} int payload; }};
extern "C" Both* object();
"""
        body = """
int Left::pad() { return pad_value; }
int Both::anchor() { return payload + 2; }
int Right::first() { return marker; }
int Right::second() { return marker + 1; }
Both* object() {
    static Both b;
    b.pad_value = 41; b.marker = 17; b.payload = 29;
    return &b;
}
"""
        if variant == "primary_override":
            body += "int Both::pad() { return payload; }"
        if variant == "insert":
            body += "int Right::extra() { return marker + 2; }"
        if variant == "override":
            body += "int Both::first() { return payload; }"
        if variant == "control":
            body = body.replace("return marker;", "return marker + 0;")
        # In the moved-base case use only the first slot, which exists on both bases.
        client = "Right* r = object(); Left* l = object(); return argc == 1 ? r->first() : argc == 2 ? r->second() : l->pad();"
    return header, body, client


def call_signature(ir, name):
    line = next(
        line
        for line in ir.splitlines()
        if line.startswith("define ") and f"@{name}(" in line
    )
    ret, arguments = line.split(f"@{name}(", 1)
    # Fixture arguments have scalar or pointer IR types. Include every argument
    # so a new compiler-emitted parameter cannot disappear from the comparison.
    params = (
        arguments.rsplit(")", 1)[0].split(", ") if not arguments.startswith(")") else []
    )
    hidden_result = bool(params and "sret(" in params[0])
    if any("sret(" in param for param in params[1:]):
        raise ValueError(f"{name}: unexpected result-pointer position")
    return ret.split()[-1], [param.split()[0] for param in params], hidden_result


def check_calls(ir, variant):
    indirect = variant == "nontrivial"
    slot = "ptr" if indirect else "i32"
    expected = {
        "_Z9tv_resultv": ("void", ["ptr"], True) if indirect else ("i32", [], False),
        "_Z6tv_get2Tv": ("i32", [slot], False),
        "_Z7tv_lateid2Tv": ("void", ["ptr", "i32", "double", slot], True),
        "_Z10tv_pointerP2Tv": ("i32", ["ptr"], False),
        "_Z12tv_referenceR2Tv": ("i32", ["ptr"], False),
    }
    observed = {name: call_signature(ir, name) for name in expected}
    if observed != expected:
        raise ValueError(f"compiler call signatures: {observed}")
    lane_return = "{ i64, i64 }" if variant == "register_class" else "{ double, i64 }"
    if not re.search(
        r"^define [^\n]*" + re.escape(lane_return) + r" @_Z5lanesv\(", ir, re.M
    ):
        raise ValueError(f"expected lanes() to return {lane_return}")
    observed["_Z5lanesv"] = (lane_return, [], False)
    return observed


def check_verdict(family, variant, verdict, code):
    compatible = variant in {"control", "primary_override"} or family in {
        "callback_reference",
        "callback_pointer_return",
        "callback_large_return",
    }
    valid = (
        (code == 0 and verdict in {"NO_CHANGE", "COMPATIBLE"})
        if compatible
        else (code == 4 and verdict == "BREAKING")
    )
    if not valid:
        raise ValueError(
            f"{family}/{variant}: expected {'compatible' if compatible else 'breaking'} verdict, got {verdict} (exit {code})"
        )


def check_report(family, variant, data, code, verdict_only=False):
    check_verdict(family, variant, data["verdict"], code)
    if verdict_only:
        return {"verdict": data["verdict"]}
    changes = data["changes"]
    kinds = sorted({c["kind"] for c in changes})
    row = dict(verdict=data["verdict"], kinds=kinds)
    if variant == "nontrivial":
        attributed = {
            (c["symbol"], c["old_value"], c["new_value"])
            for c in changes
            if c["kind"] == "value_abi_trait_changed"
        }
        returns = {
            s
            for c in changes
            if c["kind"] == "struct_return_convention_changed"
            for s in c["affected_symbols"]
        }
        if attributed != {
            ("_Z6tv_get2Tv", "p0:trivial", "p0:nontrivial"),
            ("_Z7tv_lateid2Tv", "p2:trivial", "p2:nontrivial"),
        } or returns != {"_Z9tv_resultv"}:
            raise ValueError(f"call attribution: {attributed}, {returns}")
    # Match the affected entity and direction, so an unrelated breaking
    # finding cannot satisfy the experiment.
    facts = {
        (c["kind"], c["symbol"], c.get("old_value"), c.get("new_value"))
        for c in changes
    }
    offsets = {
        ("type_field_offset_changed", "point", "0", "32"),
        ("type_field_offset_changed", "point", "32", "0"),
    }
    first, second = "_ZN5Right5firstEv", "_ZN5Right6secondEv"
    original = f"{first}, {second}"
    required = {
        ("callback_value", "change"): {
            (
                "value_abi_trait_changed",
                "dispatch",
                "p0.callback.p0:trivial",
                "p0.callback.p0:nontrivial",
            )
        },
        ("callback_return", "change"): {
            (
                "value_abi_trait_changed",
                "dispatch",
                "p0.callback.return:trivial",
                "p0.callback.return:nontrivial",
            )
        },
        ("callback_table", "passing"): {
            (
                "value_abi_trait_changed",
                "slots",
                "return.second.callback.p0:nontrivial",
                "return.second.callback.p0:trivial",
            )
        },
        ("callback_table", "swap"): {
            ("type_field_offset_changed", "Table", "0", "64"),
            ("type_field_offset_changed", "Table", "64", "0"),
        },
        ("layout", "swap"): offsets,
        ("layout", "alignment"): {
            ("type_alignment_changed", "point", "32", "64"),
            ("type_field_offset_changed", "envelope", "32", "64"),
        },
        ("virtual", "insert"): {
            ("type_vtable_changed", "Right", original, f"_ZN5Right5extraEv, {original}")
        },
        ("virtual", "swap"): {
            ("type_vtable_changed", "Right", original, f"{second}, {first}")
        },
        ("virtual", "move_base"): {
            ("base_class_offset_changed", "Both", "0", "128"),
            ("base_class_offset_changed", "Both", "128", "0"),
        },
        ("virtual", "override"): {
            ("vtable_slot_count_changed", "_ZTV4Both", "64", "72")
        },
        ("virtual", "primary_override"): {
            ("func_added", "_ZN4Both3padEv", None, "pad")
        },
        ("calls", "register_class"): {
            (
                "struct_return_convention_changed",
                "Lane",
                "regs(SSE,INTEGER)",
                "regs(INTEGER,INTEGER)",
            )
        },
        ("calls", "nontrivial"): {
            ("struct_return_convention_changed", "Tv", "regs(INTEGER)", "memory (sret)")
        },
    }.get((family, variant), set())
    if required - facts:
        raise ValueError(f"{family}/{variant}: missing {required - facts}")
    row["required_findings"] = sorted(required)
    if variant == "assignment":
        valid = not {
            "value_abi_trait_changed",
            "struct_return_convention_changed",
        }.intersection(kinds)
    elif variant == "control" or family in {
        "callback_reference",
        "callback_pointer_return",
        "callback_large_return",
    }:
        valid = data["verdict"] == "NO_CHANGE" and not kinds
    else:
        valid = True
    if not valid:
        raise ValueError(f"{family}/{variant}: {row}")
    return row


def run(out, compiler, frontend, families=FAMILIES, verdict_only=False):
    if (sys.platform, platform.machine()) != ("linux", "x86_64"):
        raise ValueError("the calling convention oracle requires Linux x86-64")
    out = out.resolve()
    receipt = out / "receipt.json"
    receipt.unlink(missing_ok=True)
    cli = [sys.executable, "-m", "abicheck"]
    env = {**os.environ, "ABICHECK_AST_FRONTEND": frontend}
    receipts = {}
    for family in families:
        variants = FAMILIES[family]
        for variant in ("old", *variants):
            folder = out / family / variant
            folder.mkdir(parents=True, exist_ok=True)
            header, body, client = sources(family, variant)
            api = folder / "api.hpp"
            api.write_text(header)
            src = folder / "api.cpp"
            src.write_text('#include "api.hpp"\n' + body)
            lib = folder / "librep.so"
            build = [compiler, "-std=c++17", "-Wno-return-type-c-linkage"]
            if family.startswith("callback_"):
                build.append("-fno-exceptions")
            subprocess.run(
                [*build, "-shared", "-fPIC", "-s", src, "-o", lib], check=True
            )
            exe = out / family / "client"
            if variant == "old":
                consumer = folder / "client.cpp"
                consumer.write_text(
                    '#include "api.hpp"\nint main(int argc, char**) {' + client + "}\n"
                )
                subprocess.run(
                    [*build, consumer, "-L", folder, "-lrep", "-o", exe], check=True
                )
            workloads = [[]]
            if family == "virtual" and variant != "move_base":
                workloads += [["second"], ["primary", "slot"]]
            if family == "layout":
                workloads = [["offset"]] if variant == "alignment" else [[], ["offset"]]
            exits = [
                subprocess.run(
                    [exe, *args], env={**env, "LD_LIBRARY_PATH": str(folder)}
                ).returncode
                for args in workloads
            ]
            expected = {
                "callback_value": [int(variant == "change")],
                "callback_reference": [0],
                "callback_return": [int(variant == "change")],
                "callback_pointer_return": [0],
                "callback_large_return": [0],
                "callback_table": [{"passing": 23, "swap": 19}.get(variant, 17)],
                "layout": {"swap": [29, 4], "alignment": [8]}.get(variant, [17, 4]),
                "calls": [34],
                "virtual": {
                    "move_base": [41],
                    "insert": [19, 17, 41],
                    "swap": [18, 17, 41],
                    "override": [29, 18, 41],
                    "primary_override": [17, 18, 29],
                }.get(variant, [17, 18, 41]),
            }[family]
            if exits != expected:
                raise ValueError(
                    f"{family}/{variant}: frozen client {exits}, expected {expected}"
                )
            row = {"exits": exits}
            if family == "calls":
                ir = subprocess.check_output(
                    [*build, "-S", "-emit-llvm", src, "-o", "-"], text=True
                )
                row["signatures"] = check_calls(ir, variant)
            if family in {
                "callback_return",
                "callback_pointer_return",
                "callback_large_return",
            }:
                ir = subprocess.check_output(
                    [*build, "-S", "-emit-llvm", src, "-o", "-"], text=True
                )
                calls = re.findall(
                    r"\bcall (?:noundef )?(void|ptr|i64) %[-\w.]+\(([^\n]*)", ir
                )
                indirect = family == "callback_large_return" or (
                    family == "callback_return" and variant == "change"
                )
                kind = (
                    "void"
                    if indirect
                    else ("ptr" if family == "callback_pointer_return" else "i64")
                )
                if (
                    len(calls) != 1
                    or calls[0][0] != kind
                    or ("sret(" in calls[0][1]) != indirect
                ):
                    raise ValueError(
                        f"{family}/{variant}: unexpected callback lowering {calls}"
                    )
                row["callback_return"] = "sret" if indirect else kind
            snap = folder / "snapshot.json"
            subprocess.run(
                [*cli, "dump", lib, "-H", api, "-o", snap],
                env=env,
                check=True,
                capture_output=True,
            )
            if variant != "old":
                report = folder / "report.json"
                proc = subprocess.run(
                    [
                        *cli,
                        "compare",
                        folder.parent / "old/snapshot.json",
                        snap,
                        "-o",
                        f"json={report}",
                    ],
                    env=env,
                    capture_output=True,
                )
                data = json.loads(report.read_text())
                row.update(
                    check_report(family, variant, data, proc.returncode, verdict_only)
                )
            receipts[f"{family}/{variant}"] = row
    receipt.write_text(json.dumps(receipts, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--frontend", choices=("clang", "castxml"), default="clang")
    parser.add_argument(
        "--family",
        choices=FAMILIES,
        action="append",
        help="run only this family (repeatable)",
    )
    parser.add_argument(
        "--verdict-only",
        action="store_true",
        help="check the public verdict and exit code independently of diagnostic attribution",
    )
    args = parser.parse_args()
    run(
        args.out,
        args.compiler,
        args.frontend,
        args.family or FAMILIES,
        args.verdict_only,
    )
