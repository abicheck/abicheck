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

"""``compat_html=True`` must also carry ``env_matrix_source_sha256``.

Codex review, P2, round 11 (PR #1221): an earlier round's fix
(``tests/test_compare_env_matrix_digest_formats.py``) claimed the
declared-deployment-floor digest is projected into every supported
``compare`` output format, HTML included -- but that test drives HTML only
through ``service_render.render_output``, which always calls
``html_report.generate_html_report`` with its default ``compat_html=False``.
There is a *second* HTML code path: the ABICC-compatible clone layout
produced by ``generate_html_report(..., compat_html=True)`` /
``report.render_html_document._render_compat_html_document``, which never
read ``env_matrix_source_sha256`` at all -- so a ``compat_html=True`` report
silently omitted an active deployment contract a ``compat_html=False``
report on the identical result showed. Mirrors the fixed test's own
fixture/assertions, calling ``generate_html_report`` directly with
``compat_html=True`` since that mode has no route through
``render_output``.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.html_report import generate_html_report
from abicheck.model import AbiSnapshot


def _snapshot_with_glibc_dependency() -> AbiSnapshot:
    elf = ElfMetadata(
        machine="EM_X86_64",
        hash_styles=frozenset({"gnu"}),
        needed=["libc.so.6"],
        versions_required={"libc.so.6": ["GLIBC_2.34"]},
    )
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        elf=elf,
        elf_only_mode=True,
        platform="elf",
    )


def _result_with_env_matrix():
    snap = _snapshot_with_glibc_dependency()
    matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
    return compare(snap, snap, env_matrix=matrix)


def _result_without_env_matrix():
    snap = _snapshot_with_glibc_dependency()
    return compare(snap, snap)


def test_compat_html_projects_the_env_matrix_digest_when_present() -> None:
    result = _result_with_env_matrix()
    assert result.env_matrix_source_sha256 is not None, "fixture precondition"

    html = generate_html_report(result, compat_html=True)

    assert result.env_matrix_source_sha256 in html
    assert "Deployment Floor Digest" in html


def test_compat_html_omits_the_env_matrix_digest_when_absent() -> None:
    result = _result_without_env_matrix()
    assert result.env_matrix_source_sha256 is None, "fixture precondition"

    html = generate_html_report(result, compat_html=True)

    assert "Deployment Floor Digest" not in html


def test_native_and_compat_html_agree_on_digest_presence() -> None:
    """The native layout already carried the digest (an earlier round's real
    fix); this pins that both layouts now agree on the same result, so a
    future regression narrowing to only one of the two layouts is caught."""
    result = _result_with_env_matrix()

    native_html = generate_html_report(result, compat_html=False)
    compat_html_text = generate_html_report(result, compat_html=True)

    assert result.env_matrix_source_sha256 in native_html
    assert result.env_matrix_source_sha256 in compat_html_text
