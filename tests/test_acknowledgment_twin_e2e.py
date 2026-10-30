"""End to end: a ``symbol:`` acknowledgment over a removal and its dedup twin.

With headers (``-H``) the header-tier removal and its ELF-only twin both match
a ``symbol:`` + ``change_kind:`` record. The dedup pass folds the twin into the
removal, so they are one observation: the run must complete, acknowledge the
removal (and its roll-up, the only other finding the record covers), keep the
break gating under the default ``breaks: gate``, and write its report. Before
the fix the matcher counted both and raised ``AmbiguousAcknowledgmentError``, so
no report was written.

Negative control: one ``symbol:`` record over two genuinely different findings
(two field-type changes of one struct) still raises, and writes no report.

Linux only: the libraries are built with gcc.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command, abicheck_env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux") or shutil.which("gcc") is None,
        reason="builds ELF libraries with gcc; Linux only",
    ),
]

_KEEP_FN = "int keep_fn(void) { return 1; }\n"
_KEEP_FN_HDR = "int keep_fn(void);\n"


def _build(tmp: Path, name: str, src: str) -> Path:
    source = tmp / f"{name}.c"
    source.write_text(src)
    lib = tmp / f"lib{name}.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O0", str(source), "-o", str(lib)], check=True
    )
    return lib


def _write(tmp: Path, name: str, body: str) -> Path:
    path = tmp / f"{name}.yml"
    path.write_text(body)
    return path


def _run(
    tmp: Path,
    *,
    old_lib: Path,
    new_lib: Path,
    headers: tuple[Path, Path] | None,
    ack: Path,
    tag: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    report = tmp / f"report-{tag}.json"
    args = [
        "compare",
        str(old_lib),
        str(new_lib),
        "--acknowledgments",
        str(ack),
        "-o",
        f"json={report}",
    ]
    if headers is not None:
        args += ["-H", f"old={headers[0]}", "-H", f"new={headers[1]}"]
    proc = subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        env=abicheck_env(),
        check=False,
    )
    return proc, report


# (subject, base change kind, old source, old header, ack record body)
_REMOVALS = {
    "function": (
        "gone_fn",
        "func_removed",
        "int gone_fn(void) { return 2; }\n",
        "int gone_fn(void);\n",
    ),
    "variable": (
        "gone_var",
        "var_removed",
        "int gone_var = 2;\n",
        "extern int gone_var;\n",
    ),
}


@pytest.mark.parametrize("with_headers", [False, True], ids=["headerless", "headers"])
@pytest.mark.parametrize("subject", sorted(_REMOVALS))
def test_acknowledged_removal_completes_with_its_twin(
    tmp_path: Path, subject: str, with_headers: bool
) -> None:
    symbol, base_kind, old_src, old_hdr = _REMOVALS[subject]
    old_lib = _build(tmp_path, "old", _KEEP_FN + old_src)
    new_lib = _build(tmp_path, "new", _KEEP_FN)
    ack = _write(
        tmp_path,
        "ack",
        "version: 1\nacknowledgments:\n"
        f"  - symbol: {symbol}\n"
        f"    change_kind: {base_kind}\n"
        "    reason: deliberate removal\n",
    )
    headers = None
    if with_headers:
        old_h = tmp_path / "old.h"
        old_h.write_text(_KEEP_FN_HDR + old_hdr)
        new_h = tmp_path / "new.h"
        new_h.write_text(_KEEP_FN_HDR)
        headers = (old_h, new_h)

    proc, report_path = _run(
        tmp_path,
        old_lib=old_lib,
        new_lib=new_lib,
        headers=headers,
        ack=ack,
        tag=f"{subject}-{with_headers}",
    )

    assert "AmbiguousAcknowledgmentError" not in proc.stderr
    assert report_path.exists(), proc.stderr
    report = json.loads(report_path.read_text())
    # Exit code: the acknowledged break still gates (breaks: gate is the default).
    assert proc.returncode == 4
    # Verdict: still BREAKING, the acknowledgment does not hide the break.
    assert report["verdict"] == "BREAKING"
    # Acknowledged count: the removal and its roll-up, one observation each,
    # with or without headers. The folded twin is not a second acknowledgment.
    assert report["disposition_audit"]["acknowledged_total"] == 2
    [entry] = report["disposition_audit"]["acknowledgments"]
    assert entry["matched_count"] == 2
    # Record before disposing: the removal stays visible as a breaking change.
    removal = [c for c in report["changes"] if c["symbol"] == symbol]
    assert [c["severity"] for c in removal] == ["breaking"]
    assert removal[0]["kind"] in {base_kind, f"{base_kind}_elf_only"}


def test_one_record_over_two_different_findings_still_raises(tmp_path: Path) -> None:
    """Negative control: two field-type changes of one struct are two findings.

    The record names the type and the kind, so it covers both field changes.
    Each has its own identity, so the strict run must still raise rather than
    accept both by one line.
    """
    old_hdr = tmp_path / "old.h"
    old_hdr.write_text("struct S { int a; int b; };\nint keep(struct S *s);\n")
    new_hdr = tmp_path / "new.h"
    new_hdr.write_text("struct S { long a; long b; };\nint keep(struct S *s);\n")
    old_lib = _build(
        tmp_path,
        "old",
        "struct S { int a; int b; };\nint keep(struct S *s) { return s->a + s->b; }\n",
    )
    new_lib = _build(
        tmp_path,
        "new",
        "struct S { long a; long b; };\n"
        "int keep(struct S *s) { return (int)(s->a + s->b); }\n",
    )
    ack = _write(
        tmp_path,
        "ack",
        "version: 1\nacknowledgments:\n"
        "  - symbol: S\n"
        "    change_kind: type_field_type_changed\n"
        "    reason: accepted\n",
    )

    proc, report_path = _run(
        tmp_path,
        old_lib=old_lib,
        new_lib=new_lib,
        headers=(old_hdr, new_hdr),
        ack=ack,
        tag="ambiguous",
    )

    assert proc.returncode != 0
    assert "AmbiguousAcknowledgmentError" in proc.stderr
    assert not report_path.exists()
