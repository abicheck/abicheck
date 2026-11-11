"""Shared oracle, registry and seeds for the parser fuzz tests (``test_parser_fuzz_oracle.py``).

The oracle, applied to every mutated input a parser is given:

1. **Bounded, typed failure.** The parser either returns, or raises one of the
   exception types it is allowed to raise on corrupt input. Any other exception
   type is an unexpected failure. A call that runs past ``budget_s`` is a hang:
   it is interrupted (``SIGALRM`` where available, a watchdog thread otherwise)
   and fails instead of hanging the test run.
2. **No silent loss.** A seed that holds facts (exports, structs, declarations)
   must not come back *empty and clean*. For the export-table parsers
   (``export_index`` given) "clean" is judged the way production judges it:
   the ADR-079 :class:`~abicheck.model.export_read.ExportReadState` computed
   from the recorded failures (``export_read_state``) or, with none recorded,
   from the block itself (``platform_export_read``). Only a ``partial`` or
   ``failed`` state excuses a loss; a stray sink write that leaves the read
   ``complete`` does not.
3. **No silent partial loss under truncation.** A truncated file is always a
   damaged file, so with ``strict_loss`` a result holding *fewer* facts than the
   seed (exports 3 -> 1) must also be excused by a ``partial``/``failed`` state.
   Byte flips are exempt from this rule: a flip can turn a valid file into a
   different valid file with fewer symbols, which no oracle short of a
   reference decoder can tell apart from damage.

What the oracle decides is the failure class the recurring fixes #1648, #1649,
#1670, #1654, #1912 and #1914 share: corrupted input read as empty or clean with
no signal.

The registry (:data:`ENROLLED`, :data:`NOT_ENROLLED`) names every parser entry
point and the fuzz tests that cover it. ``test_parser_fuzz_enrollment.py``
discovers parser entry points in ``abicheck/`` and fails on one that is in
neither table.
"""

from __future__ import annotations

import contextvars
import json
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from abicheck.extract.parse_failures import recording_export_read_failures
from abicheck.model.export_read import ExportReadState, export_read_state

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

_EXCUSED = (ExportReadState.PARTIAL, ExportReadState.FAILED)


@dataclass(frozen=True)
class Outcome:
    #: ``clean`` | ``recorded:<partial|failed>`` | ``raised:<ExceptionType>``
    status: str
    facts: int


class OracleViolation(AssertionError):
    pass


class _BudgetExceeded(BaseException):
    """Raised into a parser that ran past its budget.

    A ``BaseException`` so a parser's blanket ``except Exception`` (PE has one)
    cannot swallow the interrupt and return an empty block instead.
    """


def enforce(
    name: str,
    run: Callable[[], Any],
    facts: Callable[[Any], int],
    *,
    seed_facts: int,
    allowed: tuple[type[BaseException], ...] = (),
    export_index: Callable[[Any], Any] | None = None,
    strict_loss: bool = False,
    budget_s: float = TIME_BUDGET_S,
) -> Outcome:
    """Run *run* once and enforce the oracle. Raises :class:`OracleViolation`.

    ``allowed`` is the exception types the parser may raise on corrupt input.
    ``export_index`` (export-table parsers only) maps the result to its
    ``RawExportIndex``, which makes the ADR-079 read state the judge of loss.
    ``strict_loss`` turns on check 3 (the mutation was a truncation).
    """
    with recording_export_read_failures() as sink:
        try:
            # The fact count runs inside the classified block: a lazily decoded
            # result can fail on first access, and that failure is the parser's.
            result, n = call_with_budget(lambda: _run_and_count(run, facts), budget_s)
        except _BudgetExceeded:
            raise OracleViolation(
                f"{name}: hang (ran past the {budget_s}s budget)"
            ) from None
        except allowed as exc:
            return Outcome(f"raised:{type(exc).__name__}", 0)
        except Exception as exc:  # noqa: BLE001 - classify, do not swallow
            raise OracleViolation(
                f"{name}: unexpected {type(exc).__name__}: {exc}"
            ) from exc
    if export_index is not None:
        from abicheck.model.export_index import platform_export_read

        state = (
            export_read_state(failure_noted=True, exports_read=n > 0)
            if sink
            else platform_export_read(result, export_index(result))
        )
        if state in _EXCUSED:
            return Outcome(f"recorded:{state.value}", n)
    elif sink:
        raise OracleViolation(f"{name}: failure recorded by a parser with no channel")
    if seed_facts > 0 and n == 0:
        raise OracleViolation(
            f"{name}: silent empty read (seed had {seed_facts} facts, result has 0, "
            "read not recorded as partial or failed)"
        )
    if strict_loss and n < seed_facts:
        raise OracleViolation(
            f"{name}: silent partial loss (seed had {seed_facts} facts, truncated "
            f"input has {n}, read not recorded as partial or failed)"
        )
    return Outcome("clean", n)


def _run_and_count(
    run: Callable[[], Any], facts: Callable[[Any], int]
) -> tuple[Any, int]:
    result = run()
    return result, facts(result)


def call_with_budget(
    fn: Callable[[], Any], budget_s: float, *, prefer_signal: bool = True
) -> Any:
    """Call *fn*; raise :class:`_BudgetExceeded` if it runs past *budget_s*.

    On POSIX in the main thread a ``SIGALRM`` interrupts the call itself; the
    timer already armed (pytest-timeout's per-test valve) is restored after,
    less the time spent. Elsewhere the call runs in a daemon thread that is
    abandoned on overrun: the test fails, the thread is left to finish.
    """
    if (
        prefer_signal
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    ):
        return _call_with_alarm(fn, budget_s)
    return _call_with_watchdog(fn, budget_s)


def _call_with_alarm(fn: Callable[[], Any], budget_s: float) -> Any:
    def _on_alarm(signum: int, frame: Any) -> None:
        raise _BudgetExceeded

    t0 = time.monotonic()
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    prev_delay, prev_interval = signal.setitimer(signal.ITIMER_REAL, budget_s)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if prev_delay:
            remaining = prev_delay - (time.monotonic() - t0)
            signal.setitimer(signal.ITIMER_REAL, max(remaining, 0.001), prev_interval)


def _call_with_watchdog(fn: Callable[[], Any], budget_s: float) -> Any:
    box: dict[str, Any] = {}
    # Copy the context so the export-read sink set by the caller is visible.
    ctx = contextvars.copy_context()

    def _target() -> None:
        try:
            box["value"] = ctx.run(fn)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    worker = threading.Thread(target=_target, daemon=True, name="parser-fuzz-call")
    worker.start()
    worker.join(budget_s)
    if worker.is_alive():
        raise _BudgetExceeded
    if "error" in box:
        raise box["error"]
    return box["value"]


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


def build_macho_seed(work: Path) -> bytes:
    """A hand-built x86_64 dylib: three exports in both the nlist and the trie."""
    from _macho_synthetic import N_EXT, N_SECT, MachoSymbol, write_dylib

    names = ("_add", "_dist2", "_g_counter")
    path = write_dylib(
        work / "seed.dylib",
        [MachoSymbol(n, N_EXT | N_SECT) for n in names],
        export_trie=[(n, 0) for n in names],
    )
    return path.read_bytes()


CASTXML_SEED = """<?xml version="1.0"?>
<CastXML format="1.3">
  <Namespace id="_1" name="::" members="_2 _3 _4"/>
  <Function id="_2" name="add" returns="_5" context="_1" location="f0:1" file="f0" line="1" mangled="_Z3addii"/>
  <Function id="_3" name="dist2" returns="_5" context="_1" location="f0:2" file="f0" line="2" mangled="_Z5dist2"/>
  <Struct id="_4" name="point" context="_1" location="f0:3" file="f0" line="3" size="64"/>
  <FundamentalType id="_5" name="int" size="32" align="32"/>
</CastXML>
"""
CASTXML_EXPORTS = frozenset({"_Z3addii", "_Z5dist2"})

CLANG_HEADER = "include/seed.h"
CLANG_EXPORTS = frozenset({"_Z3addii", "_Z3negi", "g_counter"})


def _clang_fn(
    name: str, mangled: str, qualtype: str, params: tuple[str, ...]
) -> dict[str, Any]:
    return {
        "kind": "FunctionDecl",
        "name": name,
        "mangledName": mangled,
        "loc": {"file": CLANG_HEADER, "line": 1},
        "type": {"qualType": qualtype},
        "inner": [
            {"kind": "ParmVarDecl", "name": p, "type": {"qualType": "int"}}
            for p in params
        ],
    }


def clang_seed_document() -> dict[str, Any]:
    """A ``clang -ast-dump=json`` tree: two functions, a struct, a variable, an enum."""
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            _clang_fn("add", "_Z3addii", "int (int, int)", ("a", "b")),
            _clang_fn("neg", "_Z3negi", "int (int)", ("a",)),
            {
                "kind": "CXXRecordDecl",
                "name": "point",
                "tagUsed": "struct",
                "completeDefinition": True,
                "loc": {"file": CLANG_HEADER, "line": 3},
                "inner": [
                    {"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}},
                    {"kind": "FieldDecl", "name": "y", "type": {"qualType": "int"}},
                ],
            },
            {
                "kind": "VarDecl",
                "name": "g_counter",
                "mangledName": "g_counter",
                "loc": {"file": CLANG_HEADER, "line": 5},
                "type": {"qualType": "int"},
                "storageClass": "extern",
            },
            {
                "kind": "EnumDecl",
                "name": "color",
                "loc": {"file": CLANG_HEADER, "line": 6},
                "inner": [
                    {
                        "kind": "EnumConstantDecl",
                        "name": "RED",
                        "type": {"qualType": "color"},
                    }
                ],
            },
        ],
    }


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


# ---------------------------------------------------------------------------
# Registry: every parser entry point and the fuzz tests that cover it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Enrollment:
    #: ``module:attribute`` of the entry point.
    entry: str
    #: Test functions in ``test_parser_fuzz_oracle.py`` that fuzz it.
    tests: tuple[str, ...]
    #: Further modules whose failure-recording sites this entry reaches.
    covers: tuple[str, ...] = ()

    @property
    def module(self) -> str:
        return self.entry.partition(":")[0]


ENROLLED: tuple[Enrollment, ...] = (
    Enrollment(
        "abicheck.elf_metadata:parse_elf_metadata",
        ("test_elf_fast", "test_elf_slow"),
        covers=("abicheck.extract.elf_symbol_versions",),
    ),
    Enrollment(
        "abicheck.pe_metadata:parse_pe_metadata", ("test_pe_fast", "test_pe_slow")
    ),
    Enrollment(
        "abicheck.macho_metadata:parse_macho_metadata",
        ("test_macho_fast", "test_macho_slow"),
    ),
    Enrollment("abicheck.dwarf_metadata:parse_dwarf_metadata", ("test_dwarf_slow",)),
    Enrollment(
        "abicheck.dwarf_unified:parse_dwarf_metadata", ("test_dwarf_unified_slow",)
    ),
    Enrollment(
        "abicheck.storage.castxml_xml:parse_castxml_text",
        ("test_castxml_fast", "test_castxml_slow"),
    ),
    Enrollment(
        "abicheck.extract.headers.castxml.dumper:_CastxmlParser",
        ("test_castxml_fast", "test_castxml_slow"),
    ),
    Enrollment(
        "abicheck.dumper_clang:_ClangAstParser",
        ("test_clang_ast_fast", "test_clang_ast_slow"),
    ),
    Enrollment(
        "abicheck.serialization:load_snapshot",
        (
            "test_snapshot_bytes_fast",
            "test_snapshot_bytes_slow",
            "test_snapshot_struct_fast",
            "test_snapshot_struct_slow",
        ),
    ),
)

#: Discovered parser entry points deliberately not fuzzed yet, with the reason.
NOT_ENROLLED: dict[str, str] = {
    "abicheck.btf_metadata:parse_btf_metadata": (
        "follow-up: needs a BTF seed, which takes pahole or a BPF toolchain the "
        "default lane does not have"
    ),
    "abicheck.ctf_metadata:parse_ctf_metadata": (
        "follow-up: needs a CTF seed (gcc -gctf with libctf), not on the default lane"
    ),
    "abicheck.sycl_metadata:parse_sycl_metadata": (
        "follow-up: reads a plugin directory and needs a SYCL plugin seed"
    ),
    "abicheck.extract.headers.gcc.backend:NativeGccParser": (
        "parses no bytes: it holds HeaderAstFields already projected from the "
        "native GCC plugin's JSONL. Malformed JSONL rows are refused as "
        "SnapshotError by GccBackend.parse (tests/test_native_gcc_fact_"
        "integration.py); fuzzing the plugin protocol itself needs a real GCC "
        "plus a matching plugin, which the default lane does not have"
    ),
}


def iter_enrolled_entries() -> Iterator[str]:
    for enrollment in ENROLLED:
        yield enrollment.entry
