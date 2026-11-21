"""Usage: python profile.py WHEEL_DIRECTORY; uses Linux GNU readelf and GCC headers."""
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
WHEELS = {
    "daal-2025.10.0": "bfb6570cda5699e39670b71bd101adcee9a67048414997a479ccf5881953cfcc",
    "daal_include-2025.10.0": "2fe8a0ca5d3002d55de2418adfd76b753f33d4b79f4fe497376ec4a023c499f7",
    "libclang-18.1.1": "c533091d8a3bbf7460a00cb6c1a71da93bffe148f172c7d03b1c31fbf8aa2a0b",
}


def measure(root):
    from clang.cindex import CursorKind as K, Index, TypeKind as T

    so = next(root.rglob("libonedal.so.3"))
    include = next(root.glob("daal_include*/data/include"))
    listing = subprocess.check_output(["readelf","--dyn-syms","--wide",so],text=True)
    exports = set()
    for line in listing.splitlines():
        parts = line.split()
        if (len(parts) >= 8 and parts[3] in ("FUNC","IFUNC") and parts[4] in ("GLOBAL","WEAK")
                and parts[5] in ("DEFAULT","PROTECTED") and parts[6] != "UND"):
            exports.add(parts[7])
    gcc_include = subprocess.check_output(["gcc","-print-file-name=include"],text=True).strip()
    tu = Index.create().parse("client.cc",args=["-std=c++17","-I"+str(include),"-isystem",gcc_include],
                              unsaved_files=[("client.cc","#include <oneapi/dal.hpp>")])
    errors = [str(d) for d in tu.diagnostics if d.severity >= 3]
    if errors:
        raise ValueError(errors)
    rows = {}
    record_kinds = (K.STRUCT_DECL,K.CLASS_DECL,K.UNION_DECL)
    indirect = (T.POINTER,T.LVALUEREFERENCE,T.RVALUEREFERENCE)

    def polymorphic(decl, visited):
        definition = decl.get_definition()
        if not definition or definition.hash in visited:
            return False
        visited.add(definition.hash)
        for child in definition.get_children():
            if child.kind in (K.CXX_METHOD,K.DESTRUCTOR) and child.is_virtual_method():
                return True
            if child.kind == K.CXX_BASE_SPECIFIER and polymorphic(child.type.get_declaration(),visited):
                return True
        return False

    def walk(cur):
        for node in cur.get_children():
            if not node.location.file or not Path(node.location.file.name).is_relative_to(include):
                continue
            if node.kind in (K.NAMESPACE,K.LINKAGE_SPEC,*record_kinds):
                walk(node)
            elif node.kind in (K.CXX_METHOD,K.FUNCTION_DECL):
                if node.access_specifier.name in ("PRIVATE","PROTECTED"):
                    continue
                symbol = node.mangled_name
                if symbol not in exports:
                    continue
                args = [a.type.get_canonical() for a in node.get_arguments()]
                ret = node.result_type.get_canonical()
                at_boundary = [t.get_pointee().get_canonical() if t.kind in indirect else t
                               for t in [*args,ret]]
                poly = any(t.kind==T.RECORD and polymorphic(t.get_declaration(),set()) for t in at_boundary)
                if node.kind==K.CXX_METHOD and not node.is_static_method():
                    poly |= polymorphic(node.semantic_parent,set())
                header = Path(node.location.file.name).relative_to(include)
                owner = node.semantic_parent
                detail = "detail" in header.parts
                while owner and owner.kind != K.TRANSLATION_UNIT:
                    detail |= owner.spelling in ("detail","internal")
                    owner = owner.semantic_parent
                rows[symbol] = [symbol,str(header),
                                any(t.kind==T.RECORD for t in args),ret.kind==T.RECORD,
                                any(t.kind in indirect and t.get_pointee().get_canonical().kind in
                                    (T.FUNCTIONPROTO,T.FUNCTIONNOPROTO) for t in args),bool(poly),"detail" if detail else "public-candidate"]
    walk(tu.cursor)
    fields = ["symbol","declaring_header","record_arg_by_value","record_return_by_value",
              "direct_function_callback","visible_polymorphic_type","scope"]
    with (OUT/"matches.csv").open("w") as f:
        w = csv.writer(f,lineterminator="\n"); w.writerow(fields)
        for sym in sorted(rows): w.writerow(rows[sym])
    def count(selected):
        return {fields[i]:sum(row[i] for row in selected) for i in range(2,6)}
    counts = count(rows.values())
    public = [row for row in rows.values() if row[-1] == "public-candidate"]
    return dict(exports=len(exports),matched=len(rows),unmatched=len(exports)-len(rows),
                counts=counts,public_candidates=len(public),public_candidate_counts=count(public),compiler=subprocess.check_output(["gcc","-dumpfullversion"],text=True).strip(),
                diagnostics=[str(d) for d in tu.diagnostics],binary_sha256=hashlib.sha256(so.read_bytes()).hexdigest())


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        for prefix,digest in WHEELS.items():
            wheel,=Path(sys.argv[1]).glob(prefix+"-*.whl")
            with wheel.open("rb") as f:
                if hashlib.file_digest(f,"sha256").hexdigest()!=digest: raise ValueError(wheel.name)
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if name.endswith("/libonedal.so.3") or "/include/" in name or "/platlib/clang/" in name:
                        archive.extract(name,root)
        sys.path.insert(0,str(next(root.glob("libclang*/platlib"))))
        measured=measure(root)
    (OUT/"result.json").write_text(json.dumps(measured,indent=2)+"\n")
    print(measured)
