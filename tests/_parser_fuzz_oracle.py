"""Shared oracle and seeds for the parser fuzz tests (``test_parser_fuzz_oracle.py``).

The oracle, applied to every mutated input a parser is given:

1. **Bounded, typed failure.** The parser either returns, or raises one of the
   exception types its :class:`ParserSpec` allows. Any other exception type is
   an unexpected failure; a call that exceeds :data:`TIME_BUDGET_S` is a hang.
2. **No silent empty read.** A seed that holds facts (symbols, exports,
   elements, declarations) must not come back *empty and clean*: an empty
   result is acceptable only if the parser recorded an explicit failure through
   :func:`abicheck.extract.parse_failures.recording_export_read_failures`
   (``ParserSpec.has_failure_channel``), or raised.

Check 2 is deliberately narrower than "reads correctly". A mutation can turn a
valid file into a different valid file, and nothing short of a reference decoder
can tell those apart. What the oracle does decide is the failure class the
recurring fixes #1648, #1649, #1670, #1654, #1912 and #1914 share: corrupted
input read as empty or clean with no signal.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from abicheck.extract.parse_failures import recording_export_read_failures

TIME_BUDGET_S = 20.0
REPO_ROOT = Path(__file__).resolve().parent.parent
PE_SEED = REPO_ROOT / "tests" / "fixtures" / "containers" / "sources" / "corpus_pe.dll"

_SO_SOURCE = """
#include <stddef.h>
struct point { int x; int y; };
struct box { struct point lo; struct point hi; long tag; };
int g_counter = 3;
int add(int a, int b) { return a + b + g_counter; }
long dist2(struct point p) { return (long)p.x * p.x + (long)p.y * p.y; }
long area(struct box b) { return (long)(b.hi.x - b.lo.x) * (b.hi.y - b.lo.y) + b.tag; }
"""


@dataclass
class Outcome:
    status: str  # "raised" | "recorded" | "clean"
    fingerprint: int


class OracleViolation(AssertionError):
    pass


def enforce(
    name: str,
    run: Callable[[], Any],
    fingerprint: Callable[[Any], int],
    *,
    seed_fp: int,
    allowed: tuple[type[BaseException], ...] = (),
    has_failure_channel: bool = True,
) -> Outcome:
    """Run *run* once and enforce the oracle. Raises :class:`OracleViolation`.

    ``allowed`` is the exception types the parser may raise on corrupt input;
    the parsers under test that promise never to raise pass ``()``.
    ``has_failure_channel`` says whether the parser records an explicit
    failure through ``recording_export_read_failures``.
    """
    t0 = time.monotonic()
    with recording_export_read_failures() as sink:
        try:
            # The fingerprint runs inside the classified block: a lazily decoded
            # result can fail on first access, and that failure is the parser's.
            result = run()
            fp = fingerprint(result)
        except allowed as exc:
            _check_time(name, t0)
            return Outcome(f"raised:{type(exc).__name__}", 0)
        except Exception as exc:  # noqa: BLE001 - classify, do not swallow
            raise OracleViolation(
                f"{name}: unexpected {type(exc).__name__}: {exc}"
            ) from exc
    _check_time(name, t0)
    if sink:
        if not has_failure_channel:  # pragma: no cover - guard for misuse
            raise OracleViolation(f"{name}: failure recorded but none expected")
        return Outcome("recorded", fp)
    if seed_fp > 0 and fp == 0:
        raise OracleViolation(
            f"{name}: silent empty read (seed had {seed_fp} facts, result has 0, "
            "no failure recorded)"
        )
    return Outcome("clean", fp)


def _check_time(name: str, t0: float) -> None:
    elapsed = time.monotonic() - t0
    if elapsed > TIME_BUDGET_S:
        raise OracleViolation(f"{name}: took {elapsed:.1f}s (budget {TIME_BUDGET_S}s)")


# ---------------------------------------------------------------------------
# Mutators (pure functions of bytes / JSON trees; the strategies pick arguments)
# ---------------------------------------------------------------------------


def truncate(data: bytes, n: int) -> bytes:
    return data[:n]


def flip_byte(data: bytes, index: int, value: int) -> bytes:
    if not data:
        return data
    out = bytearray(data)
    out[index % len(out)] ^= value or 0x01
    return bytes(out)


def huge_count(data: bytes, index: int) -> bytes:
    """Overwrite four bytes with 0xFF: a count or offset field becomes ~4G."""
    if len(data) < 4:
        return data
    at = index % (len(data) - 3)
    return data[:at] + b"\xff\xff\xff\xff" + data[at + 4 :]


def json_mutate(tree: Any, op: str, path_pick: int, value: Any) -> Any:
    """Return *tree* with one structural mutation applied at a picked node."""
    nodes: list[tuple[Any, Any]] = []

    def walk(parent: Any, key: Any, node: Any) -> None:
        nodes.append((parent, key))
        if isinstance(node, dict):
            for k, v in node.items():
                walk(node, k, v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(node, i, v)

    walk(None, None, tree)
    parent, key = nodes[path_pick % len(nodes)]
    if parent is None:
        return tree
    if op == "drop" and isinstance(parent, dict):
        del parent[key]
    elif op == "drop" and isinstance(parent, list):
        del parent[key]
    elif op == "retype":
        parent[key] = value
    elif op == "huge" and isinstance(parent[key], int):
        parent[key] = 2**40
    return tree


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


def build_shared_object(work: Path, *, debug: bool) -> bytes:
    """Compile the seed shared object; ``debug=True`` adds DWARF."""
    gcc = shutil.which("gcc")
    if gcc is None:
        raise RuntimeError("gcc is required to build parser fuzz seeds")
    src = work / "seed.c"
    out = work / ("seed_dbg.so" if debug else "seed.so")
    src.write_text(_SO_SOURCE)
    flags = ["-g", "-O0"] if debug else ["-O2"]
    subprocess.run(
        [gcc, "-shared", "-fPIC", *flags, "-o", str(out), str(src)],
        check=True,
        capture_output=True,
    )
    return out.read_bytes()


CASTXML_SEED = """<?xml version="1.0"?>
<CastXML format="1.3">
  <Namespace id="_1" name="::" members="_2 _3 _4"/>
  <Function id="_2" name="add" returns="_5" context="_1" location="f0:1" file="f0" line="1" mangled="_Z3addii"/>
  <Function id="_3" name="dist2" returns="_5" context="_1" location="f0:2" file="f0" line="2" mangled="_Z5dist2"/>
  <Struct id="_4" name="point" context="_1" location="f0:3" file="f0" line="3" size="64"/>
  <FundamentalType id="_5" name="int" size="32" align="32"/>
</CastXML>
"""


def snapshot_seed_document() -> dict[str, Any]:
    from abicheck.model import AbiSnapshot, Function, Variable, Visibility
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(
        library="libseed.so",
        version="1.0",
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z2f{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(4)
        ],
        variables=[
            Variable(name="g", mangled="g", type="int", visibility=Visibility.PUBLIC)
        ],
    )
    return json.loads(snapshot_to_json(snap))
