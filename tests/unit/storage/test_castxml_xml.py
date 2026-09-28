"""The value-sharing castxml parser must build exactly the tree a plain
defusedxml parse builds, share equal attribute values, and keep defusedxml's
protections."""

from __future__ import annotations

import random

import pytest
from defusedxml import ElementTree as DefusedET, EntitiesForbidden

from abicheck.storage.castxml_xml import ARGUMENT_ATTRIBUTES_READ, parse_castxml_xml

_TAGS = ["Function", "Argument", "Typedef", "Struct", "Field", "File"]
_VALUES = ["_1", "_2", "f1", "f2", "int", "1", "", "a&amp;b", "x y", "é"]


def _shape(el):
    # `Argument` keeps only the attributes a reader consults; everything else
    # must match a plain parse exactly.
    attrs = dict(el.attrib)
    if el.tag == "Argument":
        attrs = {k: v for k, v in attrs.items() if k in ARGUMENT_ATTRIBUTES_READ}
    return (el.tag, attrs, el.text, el.tail, [_shape(c) for c in el])


def _random_doc(rng: random.Random, depth: int = 0) -> str:
    kids = "".join(
        _random_doc(rng, depth + 1)
        for _ in range(rng.randint(0, 4 if depth < 3 else 0))
    )
    attrs = " ".join(f'a{i}="{rng.choice(_VALUES)}"' for i in range(rng.randint(0, 5)))
    tag = rng.choice(_TAGS)
    text = rng.choice(["", "t", " \n "])
    return f"<{tag} {attrs}>{text}{kids}</{tag}>{rng.choice(['', 'tail'])}"


@pytest.mark.parametrize("seed", range(40))
def test_tree_is_identical_to_a_plain_defused_parse(tmp_path, seed: int) -> None:
    rng = random.Random(seed)
    doc = tmp_path / "castxml.xml"
    doc.write_text(
        f'<?xml version="1.0"?><CastXML format="1.4">{_random_doc(rng)}</CastXML>',
        encoding="utf-8",
    )
    assert _shape(parse_castxml_xml(doc)) == _shape(DefusedET.parse(str(doc)).getroot())


def test_equal_attribute_values_are_one_object(tmp_path) -> None:
    doc = tmp_path / "castxml.xml"
    doc.write_text(
        '<CastXML><Function file="f1" context="_1"/><Function file="f1" context="_1"/></CastXML>'
    )
    a, b = list(parse_castxml_xml(doc))
    assert a.get("file") is b.get("file") and a.get("context") is b.get("context")


def test_defusedxml_protections_still_apply(tmp_path) -> None:
    doc = tmp_path / "bomb.xml"
    doc.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>')
    with pytest.raises(EntitiesForbidden):
        parse_castxml_xml(doc)
