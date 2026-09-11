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

import re

import pytest

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


@pytest.mark.parametrize("compat_html", [True, False], ids=["compat", "native"])
@pytest.mark.parametrize("with_matrix", [True, False], ids=["digest", "no-digest"])
def test_no_rendered_table_contains_a_blank_row_line(
    compat_html: bool, with_matrix: bool
) -> None:
    """An optional row contributes nothing at all when it is absent.

    The three tests above are substring assertions, and a substring assertion
    cannot see what an empty optional branch leaks *around* itself: the
    compat layout interpolated an empty row onto a line of its own, so a run
    with no ``deployment:`` contract still emitted that line's newline and
    grew a stray blank line inside the "Test Info" table. ``"Deployment Floor
    Digest" not in html`` stayed true the whole time; only
    ``tests/golden/main_report_compat.html`` noticed, and only for that one
    layout and that one fixture.

    Stated structurally instead: no ``<table>`` a report renders may contain
    a blank or whitespace-only line between its rows. That holds for *any*
    conditionally-emitted row in *any* table in either layout -- it needs no
    golden file, no fixture pair, and it does not care which optional field
    grew the gap -- so the next optional row added this way fails here rather
    than silently drifting a golden.
    """
    result = _result_with_env_matrix() if with_matrix else _result_without_env_matrix()

    html = generate_html_report(result, compat_html=compat_html)

    offenders: list[tuple[int, str]] = []
    depth = 0
    for number, line in enumerate(html.splitlines(), start=1):
        opens = len(re.findall(r"<table\b", line))
        closes = line.count("</table>")
        if depth > 0 and opens == 0 and closes == 0 and not line.strip():
            offenders.append((number, line))
        depth += opens - closes
    assert offenders == [], (
        f"blank line(s) inside a rendered <table> at {offenders!r} -- an "
        "optional row is emitting its newline when it emits no row"
    )
