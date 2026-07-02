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

"""Public-roots misconfiguration diagnostic (ADR-038 Flow C, Caveat A).

Regression guard for the plugin's silent-empty-pack failure mode: when
``public-roots`` does not match how the compiler resolves the public headers,
every declaration classifies non-public and the plugin used to emit an empty
pack with exit 0 and no message — a 20-minute debug for the operator. The plugin
must now:

  * WARN (naming the count, an example rejected header, and the ``clang -H`` tip)
    when public-roots matches zero declarations though header decls were seen,
  * stay silent and emit a non-empty pack when public-roots is correct, and
  * auto-derive roots from the compile's ``-I``/``-iquote`` include dirs (with a
    one-time inference note) when no ``public-roots`` is given at all, so a
    forgotten flag yields a populated surface rather than an empty pack.

Standalone (mirrors ``conformance.py``); run by the ``clang-plugin`` workflow:

    python contrib/abicheck-clang-plugin/tests/test_public_roots_diagnostic.py \
        --plugin build/libabicheck-facts.so --clangxx clang++
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DIAG = "public-roots matched 0 declarations"


def _compile(
    work: Path,
    plugin: Path,
    clangxx: str,
    public_root: str | None,
    out_dir: Path | None = None,
    obj: str = "widget.o",
    extra_flags: list[str] | None = None,
) -> str:
    """Compile the widget fixture with *public_root*; return combined stderr.

    *out_dir* overrides the pack output directory (default: one per root) so
    several TUs can be pointed at a shared pack; *obj* names the object file so
    two invocations don't clobber each other. When *public_root* is ``None`` the
    ``public-roots=`` argument is omitted entirely, exercising the plugin's
    auto-derivation from the compile's ``-I`` include dirs. Returns
    ``stderr + "@@PACK@@" + out_dir`` — the single source of the plugin
    invocation contract.
    """
    out = (
        out_dir
        if out_dir is not None
        else work / f"out_{(public_root or 'auto').replace('/', '_')}"
    )
    argp = ["-Xclang", "-plugin-arg-abicheck-facts", "-Xclang"]
    roots_args = [] if public_root is None else [*argp, f"public-roots={public_root}"]
    proc = subprocess.run(
        [
            clangxx,
            "-std=c++17",
            "-Iinclude",
            *(extra_flags or []),
            f"-fplugin={plugin}",
            *argp,
            f"out={out}",
            *roots_args,
            "-c",
            "widget.cpp",
            "-o",
            str(work / obj),
        ],
        cwd=str(work),
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    return proc.stderr + "\n@@PACK@@" + str(out)


def _pack_entity_count(out_dir: Path) -> int:
    total = 0
    for jsonl in glob.glob(str(out_dir / "source_facts" / "*.jsonl")):
        with open(jsonl, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                for k in (
                    "functions",
                    "types",
                    "templates",
                    "inline_bodies",
                    "constexpr_values",
                    "macros",
                    "variables",
                ):
                    total += len(rec.get(k) or [])
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin", required=True, help="path to libabicheck-facts.so")
    ap.add_argument("--clangxx", default="clang++", help="clang++ to compile with")
    ap.add_argument("--keep", action="store_true", help="keep the work dir")
    args = ap.parse_args(argv)

    plugin = Path(args.plugin).resolve()
    if not plugin.is_file():
        print(f"error: plugin not found: {plugin}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="abicheck-caveatA-"))
    shutil.copytree(FIXTURES / "include", work / "include", dirs_exist_ok=True)
    shutil.copyfile(FIXTURES / "widget.cpp", work / "widget.cpp")

    failures: list[str] = []
    try:
        # 1) WRONG root: a path no header resolves under → diagnostic + empty pack.
        wrong = _compile(work, plugin, args.clangxx, "no-such-public-root")
        wrong_err, wrong_pack = wrong.split("\n@@PACK@@")
        if _DIAG not in wrong_err:
            failures.append(
                "wrong public-roots did NOT emit the diagnostic (silent empty "
                f"pack regressed). stderr was:\n{wrong_err.strip() or '<empty>'}"
            )
        if _pack_entity_count(Path(wrong_pack)) != 0:
            failures.append("wrong public-roots unexpectedly produced entities")

        # 2) CORRECT root: silent, and a non-empty public surface.
        right = _compile(work, plugin, args.clangxx, "include")
        right_err, right_pack = right.split("\n@@PACK@@")
        if _DIAG in right_err:
            failures.append(
                f"correct public-roots wrongly emitted the diagnostic:\n{right_err}"
            )
        n = _pack_entity_count(Path(right_pack))
        if n == 0:
            failures.append("correct public-roots produced an EMPTY pack")

        # 3) De-dup: two TUs with the wrong root sharing one out dir must emit the
        # human-facing stderr line only ONCE (a big -j build must not spam), while
        # each TU still records the note in its own pack diagnostics. Reuse
        # _compile() (the single owner of the plugin invocation contract) with a
        # shared out_dir and distinct object names.
        shared = work / "out_shared"
        n_warn = 0
        for obj in ("a.o", "b.o"):
            stderr, _ = _compile(
                work,
                plugin,
                args.clangxx,
                "no-such-public-root",
                out_dir=shared,
                obj=obj,
            ).split("\n@@PACK@@")
            n_warn += stderr.count(_DIAG)
        if n_warn != 1:
            failures.append(
                f"stderr warning not de-duplicated: emitted {n_warn} times across "
                "2 TUs sharing one out dir (expected exactly 1)"
            )

        # 4) AUTO-DERIVE: with NO public-roots arg at all, the plugin infers roots
        # from the compile's -I/-iquote include dirs, so the pack is non-empty and a
        # one-time inference note names how many roots it derived. This turns the
        # "forgot public-roots" trap into a populated surface instead of an empty
        # pack (ADR-038 Flow C, Caveat A).
        auto = _compile(work, plugin, args.clangxx, None)
        auto_err, auto_pack = auto.split("\n@@PACK@@")
        if "no public-roots given; inferred" not in auto_err:
            failures.append(
                "auto-derive did NOT emit the inference note when public-roots was "
                f"omitted. stderr was:\n{auto_err.strip() or '<empty>'}"
            )
        if _pack_entity_count(Path(auto_pack)) == 0:
            failures.append(
                "auto-derive produced an EMPTY pack (roots not inferred from -I)"
            )

        # 5) THIRD-PARTY EXCLUSION: an absolute -I OUTSIDE the build cwd (a
        # dependency like /opt/boost/include) must NOT be inferred as a public root
        # — only project-local include dirs are. Point a second -I at a temp dir
        # outside the work tree and assert the inference note omits it.
        outside = Path(tempfile.mkdtemp(prefix="abicheck-thirdparty-"))
        try:
            (outside / "dep.hpp").write_text("struct Dep { int z; };\n")
            tp = _compile(
                work,
                plugin,
                args.clangxx,
                None,
                out_dir=work / "out_thirdparty",
                extra_flags=[f"-I{outside}"],
            )
            tp_err, _ = tp.split("\n@@PACK@@")
            if str(outside) in tp_err:
                failures.append(
                    "auto-derive inferred a THIRD-PARTY -I dir outside the build "
                    f"cwd ({outside}); it must be excluded. stderr:\n{tp_err}"
                )
            if "inferred 1 public root" not in tp_err:
                failures.append(
                    "auto-derive should infer exactly the 1 in-tree root, not the "
                    f"outside dir. stderr:\n{tp_err}"
                )
        finally:
            shutil.rmtree(outside, ignore_errors=True)

        # 6) NO ROOTS AT ALL: a TU with header decls but no -I/-iquote to infer
        # from (and no explicit public-roots) must FAIL LOUD, not fall through to a
        # silent empty pack — the exact trap the diagnostic exists to kill.
        sub = work / "noinc"
        sub.mkdir(exist_ok=True)
        (sub / "loc.h").write_text("struct Pub { int a; };\n")
        (sub / "main.cpp").write_text('#include "loc.h"\nint useit() { return 0; }\n')
        noinc = subprocess.run(
            [
                args.clangxx,
                "-std=c++17",
                f"-fplugin={plugin}",
                "-Xclang",
                "-plugin-arg-abicheck-facts",
                "-Xclang",
                f"out={sub / 'out'}",
                "-c",
                "main.cpp",
                "-o",
                str(sub / "main.o"),
            ],
            cwd=str(sub),
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
        if "no project-local include dirs to infer from" not in noinc.stderr:
            failures.append(
                "a TU with header decls but no -I and no public-roots did NOT emit "
                f"the no-roots diagnostic (silent empty pack). stderr:\n{noinc.stderr}"
            )

        if failures:
            print("CAVEAT-A DIAGNOSTIC TEST FAILED:", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            return 1
        print(
            f"Caveat-A diagnostic test PASSED: wrong root warns + empty; "
            f"correct root silent + {n} entities."
        )
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
