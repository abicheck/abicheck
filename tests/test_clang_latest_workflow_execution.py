# Copyright 2026 Nikolay Petrov
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

"""Execute clang-latest.yml's "Download and verify pinned LLVM" step for real.

The step's trust boundary is the downloaded LLVM release: whatever it
extracts is put first on ``PATH`` and runs every clang-sensitive test. The
property that matters is behavioural -- a tarball whose bytes are not the
pinned digest must stop the step *before* anything is extracted -- so this
runs the step body (``_workflow_exec.run_step``) against a stand-in ``curl``
that serves a substituted archive, instead of asserting the YAML's text.

The positive control serves an archive whose digest *is* the one passed in:
the same step must then extract exactly the clang members, so a failure in
the hostile cases is the digest check and not an unrelated breakage.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _workflow_exec import (
    find_run_step,
    have_bash,
    make_workspace,
    outside_is_intact,
    run_step,
)

WORKFLOW = "clang-latest.yml"
STEP = "Download and verify pinned LLVM"
VERSION = "23.1.5"
TOP = f"LLVM-{VERSION}-Linux-X64"

pytestmark = [
    pytest.mark.skipif(not have_bash(), reason="needs a real bash"),
    pytest.mark.skipif(
        shutil.which("zstd") is None or not sys.platform.startswith("linux"),
        reason="needs zstd and GNU tar; the step runs on ubuntu-24.04 only",
    ),
]

_FAKE_CURL = """#!/bin/sh
# Stand-in for curl: copy $SERVED_ARCHIVE to the --output path, record the URL.
out=""
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
echo "$url" >> "$CURL_LOG"
cp "$SERVED_ARCHIVE" "$out"
"""


def _release_tree(root: Path, *, extra: dict[str, str] | None = None) -> Path:
    """A miniature release layout: the members the step extracts plus files
    it must leave behind."""
    top = root / TOP
    (top / "bin").mkdir(parents=True)
    (top / "lib" / "clang" / "23" / "include").mkdir(parents=True)
    (top / "bin" / "clang-23").write_text("#!/bin/sh\necho clang 23\n")
    (top / "bin" / "clang").symlink_to("clang-23")
    (top / "bin" / "clang++").symlink_to("clang")
    (top / "lib" / "clang" / "23" / "include" / "stddef.h").write_text("/* */\n")
    (top / "bin" / "lld").write_text("not extracted\n")
    for rel, text in (extra or {}).items():
        (top / rel).parent.mkdir(parents=True, exist_ok=True)
        (top / rel).write_text(text)
    return top


def _archive(tmp_path: Path, name: str, **tree_kw) -> Path:
    src = tmp_path / f"src-{name}"
    src.mkdir()
    _release_tree(src, **tree_kw)
    out = tmp_path / f"{name}.tar.zst"
    subprocess.run(
        ["tar", "-I", "zstd -q --long=31", "-cf", str(out), "-C", str(src), TOP],
        check=True,
    )
    return out


def _run(tmp_path: Path, served: Path, pinned_sha: str):
    base = tmp_path / "run"
    workspace = make_workspace(base)
    fake_bin = base / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text(_FAKE_CURL)
    (fake_bin / "curl").chmod(0o755)
    prefix = workspace / "llvm"
    log = base / "curl.log"
    result = run_step(
        find_run_step(WORKFLOW, "clang", STEP),
        workspace=workspace,
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "LLVM_VERSION": VERSION,
            "LLVM_SHA256": pinned_sha,
            "LLVM_PREFIX": str(prefix),
            "SERVED_ARCHIVE": str(served),
            "CURL_LOG": str(log),
        },
    )
    return result, prefix, base, log


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_positive_control_extracts_only_the_clang_members(tmp_path: Path) -> None:
    good = _archive(tmp_path, "good")
    result, prefix, base, log = _run(tmp_path, good, _sha(good))
    assert result.returncode == 0, result.stderr
    extracted = {p.relative_to(prefix).as_posix() for p in prefix.rglob("*")}
    assert extracted == {
        "bin",
        "bin/clang",
        "bin/clang++",
        "bin/clang-23",
        "lib",
        "lib/clang",
        "lib/clang/23",
        "lib/clang/23/include",
        "lib/clang/23/include/stddef.h",
    }
    assert log.read_text().strip() == (
        "https://github.com/llvm/llvm-project/releases/download/"
        f"llvmorg-{VERSION}/{TOP}.tar.zst"
    )
    assert outside_is_intact(base)


@pytest.mark.parametrize(
    "variant",
    [
        # Same layout, one extra file: a different archive, a different digest.
        pytest.param(
            {"extra": {"bin/clang-23": "#!/bin/sh\necho pwned\n"}}, id="swapped-binary"
        ),
        pytest.param(
            {"extra": {"lib/clang/23/include/evil.h": "#error"}}, id="added-header"
        ),
    ],
)
def test_archive_not_matching_the_pinned_digest_extracts_nothing(
    tmp_path: Path, variant: dict
) -> None:
    good = _archive(tmp_path, "good")
    hostile = _archive(tmp_path, "hostile", **variant)
    assert _sha(hostile) != _sha(good)
    result, prefix, base, _ = _run(tmp_path, hostile, _sha(good))
    assert result.returncode != 0
    assert "FAILED" in result.stdout + result.stderr  # sha256sum -c's verdict
    assert not prefix.exists(), "nothing may be extracted from an unverified archive"
    assert outside_is_intact(base)


def test_truncated_download_extracts_nothing(tmp_path: Path) -> None:
    good = _archive(tmp_path, "good")
    truncated = tmp_path / "truncated.tar.zst"
    truncated.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    result, prefix, base, _ = _run(tmp_path, truncated, _sha(good))
    assert result.returncode != 0
    assert not prefix.exists()
    assert outside_is_intact(base)
