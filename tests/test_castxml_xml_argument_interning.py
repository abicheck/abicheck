"""``parse_castxml_xml`` prunes and shares ``Argument`` elements.

That is only correct if (a) no reader consults an ``Argument`` attribute
outside ``ARGUMENT_ATTRIBUTES_READ``, (b) nothing mutates a parsed castxml
tree, and (c) the parser's output is identical to a plain parse. Each is
stated here: (a)/(b) against the readers' own source, (c) as a differential
oracle against ``defusedxml.ElementTree.parse`` over generated documents and,
under ``integration``, a real castxml run.
"""

from __future__ import annotations

import ast
import dataclasses
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse as plain_parse

from abicheck.storage.castxml_xml import ARGUMENT_ATTRIBUTES_READ, parse_castxml_xml

_REPO = Path(__file__).resolve().parents[1]
_READERS = [
    _REPO / "abicheck" / "extract" / "headers" / "castxml",
    _REPO / "abicheck" / "buildsource" / "source_extractors" / "castxml.py",
]


def _reader_files() -> list[Path]:
    out: list[Path] = []
    for p in _READERS:
        out.extend(sorted(p.rglob("*.py")) if p.is_dir() else [p])
    return out


class TestReadersHonourTheContract:
    def test_argument_attributes_read_are_all_kept(self) -> None:
        """Every ``.get("k")`` on a loop variable bound over a castxml element's
        children, inside a block that tests ``.tag == "Argument"``, must name a
        kept attribute."""
        read: set[str] = set()
        for path in _reader_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                test = ast.unparse(node.test)
                if "'Argument'" not in test:
                    continue
                for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "get"
                        and sub.args
                        and isinstance(sub.args[0], ast.Constant)
                        and isinstance(sub.args[0].value, str)
                    ):
                        read.add(sub.args[0].value)
        assert read, "found no Argument reader at all -- the scan is broken"
        assert read <= ARGUMENT_ATTRIBUTES_READ, read - ARGUMENT_ATTRIBUTES_READ

    def test_no_reader_mutates_a_parsed_tree(self) -> None:
        mutators = {
            "set",
            "append",
            "extend",
            "insert",
            "remove",
            "clear",
            "makeelement",
        }
        hits = []
        for path in _reader_files():
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in mutators
                    and ast.unparse(node.func.value).split(".")[-1]
                    in {"el", "arg", "child", "root", "elem", "element", "node"}
                ):
                    hits.append(f"{path.name}:{node.lineno}")
                if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                    if ast.unparse(node.value).endswith("attrib"):
                        hits.append(f"{path.name}:{node.lineno}")
        assert not hits, hits


def _doc(rng: random.Random, n_funcs: int) -> str:
    types = [f"_{i}" for i in range(1, 12)]
    names = ["", "n", "x", "a", "lda", "info"]
    lines = ['<?xml version="1.0"?>', '<CastXML format="1.1.0">']
    for f in range(n_funcs):
        lines.append(
            f'  <Function id="f{f}" name="fn{f}" returns="_1" context="_0" file="f1" line="{f}">'
        )
        for a in range(rng.randint(0, 5)):
            attrs = {
                "name": rng.choice(names),
                "type": rng.choice(types),
                "file": "f1",
                "line": str(rng.randint(1, 50)),
                "location": f"f1:{rng.randint(1, 50)}",
            }
            if rng.random() < 0.2:
                attrs["default"] = rng.choice(["0", "nullptr", "1"])
            if rng.random() < 0.1:
                attrs["original_type"] = rng.choice(types)
            attr_text = " ".join(f'{k}="{v}"' for k, v in attrs.items())
            lines.append(f"    <Argument {attr_text}/>")
        if rng.random() < 0.1:
            lines.append("    <Ellipsis/>")
        lines.append("  </Function>")
    lines.append('  <File id="f1" name="/x/a.h"/>')
    lines.append("</CastXML>")
    return "\n".join(lines)


def _shape(root) -> list:
    """Order-preserving structural view restricted to what readers consult."""
    out = []
    for el in root.iter():
        attrs = dict(el.attrib)
        if el.tag == "Argument":
            attrs = {k: v for k, v in attrs.items() if k in ARGUMENT_ATTRIBUTES_READ}
        out.append((el.tag, sorted(attrs.items()), len(el)))
    return out


@pytest.mark.parametrize("seed", range(6))
def test_differential_against_plain_parse(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    p = tmp_path / "d.xml"
    p.write_text(_doc(rng, 200))
    ours = parse_castxml_xml(p)
    assert _shape(ours) == _shape(plain_parse(str(p)).getroot())
    args = [a for f in ours for a in f if a.tag == "Argument"]
    if len(args) > 20:
        # Vacuity guard: sharing actually happened.
        assert len({id(a) for a in args}) < len(args)
    for a in args:
        assert set(a.attrib) <= ARGUMENT_ATTRIBUTES_READ


def test_argument_with_children_is_not_shared(tmp_path: Path) -> None:
    p = tmp_path / "d.xml"
    p.write_text(
        '<CastXML><Function id="a"><Argument name="x" type="_1"><Z/></Argument></Function>'
        '<Function id="b"><Argument name="x" type="_1"><Z/></Argument></Function></CastXML>'
    )
    root = parse_castxml_xml(p)
    assert root[0][0] is not root[1][0]


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_real_castxml_parser_output_is_unchanged(tmp_path: Path) -> None:
    from abicheck.extract.headers.castxml.dumper import _CastxmlParser

    hdr = tmp_path / "a.h"
    hdr.write_text(
        "#include <stddef.h>\n"
        "struct S { int a; };\n"
        + "".join(
            f"int f{i}(int n, const double* x, size_t lda, struct S* s{', int d = 0' if i % 3 == 0 else ''});\n"
            for i in range(40)
        )
        + "void v(int, ...);\n"
    )
    out = tmp_path / "a.xml"
    proc = subprocess.run(
        ["castxml", "--castxml-output=1", "-x", "c++", "-o", str(out), str(hdr)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(proc.stderr)

    def funcs(root) -> list:
        p = _CastxmlParser(
            root, set(), set(), [str(hdr)], None, no_binary_evidence=True
        )
        return sorted((dataclasses.asdict(f) for f in p.parse_functions()), key=repr)

    plain = funcs(plain_parse(str(out)).getroot())
    ours = funcs(parse_castxml_xml(out))
    assert len(plain) >= 41
    assert ours == plain
