# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`compare --used-by`: the audit's gating count and the exit code agree.

Field report: a `--used-by` run whose consumer referenced none of 2,271
breaking findings printed "0 gating" in its one-line audit and still exited
4. Workstream D-S1 (`docs/reference/exit-codes.md`, "Application- and
plugin-scoped comparisons") says the exit is always the full-library
decision, so the exit was right and the audit was not: the consumer close
demoted every finding the consumer does not use to `non_gating`, narrowing
the audit to a scoped gate that no longer decides the run.

Invariant, over consumers that use none / some / all of the broken symbols,
consumers requiring an export no library finding covers, and library pairs
with and without breaks: the run's exit code and its audit gating count are
exactly those of the same comparison with no consumer supplied (the oracle --
a different code path that never touches consumer scoping), and
`gating > 0` iff the exit is non-zero.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

import abicheck.workflows.consumer_scope as consumer_scope_mod
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.consumer_requirements import (
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
)
from abicheck.model.elf_facts import ElfMetadata
from abicheck.serialization import snapshot_to_json

_SONAME = "libfoo.so.1"


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _write(tmp_path: Path, removed: int, kept: int, extra_old: tuple[str, ...]):
    """OLD exports `gone*`+`keep*`(+extra); NEW only `keep*`."""
    gone = [_fn(f"gone{i}") for i in range(removed)]
    keep = [_fn(f"keep{i}") for i in range(kept)]
    # An `elf` block is what makes a JSON snapshot carry binary evidence,
    # which `--used-by` requires (`require_used_by_binary_evidence`).
    old = AbiSnapshot(
        library=_SONAME,
        version="1.0",
        functions=[*gone, *keep],
        from_headers=True,
        elf=ElfMetadata(soname=_SONAME),
    )
    new = AbiSnapshot(
        library=_SONAME,
        version="2.0",
        functions=keep,
        from_headers=True,
        elf=ElfMetadata(soname=_SONAME),
    )
    old_p, new_p = tmp_path / "old.json", tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    old_exports = frozenset(f.mangled for f in (*gone, *keep)) | frozenset(extra_old)
    new_exports = frozenset(f.mangled for f in keep)
    return (
        old_p,
        new_p,
        [f.mangled for f in gone],
        [f.mangled for f in keep],
        (
            old_exports,
            new_exports,
        ),
    )


def _stub_consumer(monkeypatch, app: Path, required: set[str], exports) -> None:
    """Stand in for the two binary readers: the consumer's import table and
    the libraries' export tables. Everything downstream -- requirement
    evaluation, overlays, the ledger close, the exit decision, rendering --
    runs for real."""
    old_exports, new_exports = exports

    def read_exports(lib):
        is_old = getattr(lib, "version", None) == "1.0" or str(lib).endswith("old.json")
        names = old_exports if is_old else new_exports
        return LibraryExportFacts(
            label=str(lib),
            binary_format="elf",
            soname=_SONAME,
            export_names=names,
            unversioned_exports=names,
            versions_defined=frozenset(),
        )

    def read_consumer(spec, library_name):
        return ConsumerImportFacts(
            path=app,
            binary_format="elf",
            target_library=library_name,
            requirements=AppRequirements(
                needed_libs=[_SONAME], undefined_symbols=set(required)
            ),
        )

    monkeypatch.setattr(consumer_scope_mod, "read_library_export_facts", read_exports)
    monkeypatch.setattr(consumer_scope_mod, "read_consumer_facts", read_consumer)


def _run(args: list[str]) -> tuple[int, dict, int]:
    """(exit code, JSON report, gating count printed on the one-line audit)."""
    json_res = CliRunner().invoke(main, [*args, "-o", "json=-"])
    assert json_res.exit_code in (0, 1, 2, 4), json_res.output
    report = json.loads(json_res.stdout)
    stat_res = CliRunner().invoke(main, args)
    assert stat_res.exit_code == json_res.exit_code, stat_res.output
    match = re.search(r"(\d+) gating", stat_res.output)
    assert match, f"no one-line audit gating count in:\n{stat_res.output}"
    return json_res.exit_code, report, int(match.group(1))


# (removed, kept, consumer uses: "none" | "some" | "all", extra unmatched
# export the consumer needs that no library finding covers)
_SCENARIOS = [
    pytest.param(0, 2, "none", False, id="no-break-consumer-unaffected"),
    pytest.param(0, 2, "none", True, id="no-break-consumer-needs-vanished-export"),
    pytest.param(3, 1, "none", False, id="break-consumer-uses-none"),
    pytest.param(3, 1, "some", False, id="break-consumer-uses-some"),
    pytest.param(3, 1, "all", False, id="break-consumer-uses-all"),
    pytest.param(3, 1, "none", True, id="break-consumer-needs-vanished-export"),
    pytest.param(1, 0, "all", True, id="break-consumer-uses-all-plus-overlay"),
]


@pytest.mark.parametrize(("removed", "kept", "uses", "overlay"), _SCENARIOS)
@pytest.mark.parametrize("severity", [False, True], ids=["legacy", "severity"])
def test_used_by_audit_gating_matches_the_exit(
    tmp_path, monkeypatch, removed, kept, uses, overlay, severity
) -> None:
    extra = ("_Z5ghostv",) if overlay else ()
    old_p, new_p, gone, keep, exports = _write(tmp_path, removed, kept, extra)
    required = set(keep[:1]) | set(extra)
    required |= {"none": set(), "some": set(gone[:1]), "all": set(gone)}[uses]
    app = tmp_path / "app"
    app.write_bytes(b"\x7fELF" + b"\x00" * 64)
    _stub_consumer(monkeypatch, app, required, exports)

    base = ["compare", str(old_p), str(new_p)]
    if severity:
        base += ["--set", "severity.preset=default"]

    # The oracle: the same comparison with no consumer at all.
    plain_exit, plain_report, plain_gating = _run(base)
    scoped_exit, scoped_report, scoped_gating = _run([*base, "--used-by", str(app)])

    # The consumer assessment really ran (else this proves nothing).
    assert scoped_report.get("used_by"), scoped_report.keys()

    # D-S1: --used-by changes what is reported, never the exit.
    assert scoped_exit == plain_exit
    # ...so the audit's gating count is the unscoped run's too.
    assert scoped_gating == plain_gating
    audit = scoped_report["disposition_audit"]
    assert audit["effective_total"] == scoped_gating
    # And the one-line audit can never say "0 gating" beside a failing exit.
    assert (scoped_gating > 0) == (scoped_exit != 0)
    # Independent count: every removed public function is one breaking finding.
    assert scoped_gating == removed
    assert plain_report["disposition_audit"]["effective_total"] == removed
