"""Shared access to the field-shapes corpus (``tests/field_shapes/``).

A test that reproduces a corpus shape reads its sources from here instead of
embedding a copy, so the corpus file and the older repro test cannot drift
apart. ``test_field_shapes.py`` guards that no test embeds a byte-identical
copy of a corpus source.
"""

from __future__ import annotations

from pathlib import Path

CORPUS = Path(__file__).parent / "field_shapes"

#: The files each ``v*/`` side holds and the corpus runner consumes.
SOURCE_FILES = ("api.h", "api.cpp")


def shape_dir(name: str, version: str) -> Path:
    path = CORPUS / name / version
    if not path.is_dir():
        raise FileNotFoundError(f"no field shape side {name}/{version}")
    return path


def shape_sources(name: str, version: str) -> dict[str, str]:
    """``{file name: text}`` for one side of a shape, read as UTF-8."""
    side = shape_dir(name, version)
    return {f: (side / f).read_text(encoding="utf-8") for f in SOURCE_FILES}


def materialize(
    name: str, version: str, dest: Path, sources: dict[str, str] | None = None
) -> Path:
    """Write one side's sources (or a derived ``sources`` mapping) into
    ``dest`` (created if missing) and return ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    for fname, text in (sources or shape_sources(name, version)).items():
        (dest / fname).write_text(text, encoding="utf-8")
    return dest


def replace_once(text: str, old: str, new: str) -> str:
    """Derive a variant from corpus text; fails loudly if ``old`` drifted."""
    assert text.count(old) == 1, (old, text)
    return text.replace(old, new)
