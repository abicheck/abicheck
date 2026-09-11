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

"""Every ``compare`` output format must carry ``env_matrix_source_sha256``.

Codex review, P2 (Finding 4, round 7): ``reporter.py``'s ``_build_json_base``
adds the declared-deployment-floor digest to the full/leaf JSON mapping via
``reporter_contract_blocks.add_env_matrix_digest``, but the earlier round
never checked whether the *other* ``compare`` output formats -- Markdown,
the ``review`` digest, SARIF, HTML, and JUnit -- project the same field.
They did not. This mirrors the parallel test suite already covering the
``--no-baseline`` audit report's own ``env_matrix_source_sha256``
(``tests/test_no_baseline_report_formats.py``'s
``test_every_format_projects_the_env_matrix_digest_when_present``/
``test_every_format_omits_the_env_matrix_digest_when_absent``), for the
two-sided ``compare`` report instead.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.model import AbiSnapshot
from abicheck.service_render import render_output

#: Every ``compare`` output format that renders through the shared
#: envelope/document pipeline (``service_render._SUPPORTED_FORMATS``, minus
#: the ``md`` alias of ``markdown``).
_COMPARE_SUPPORTED_FORMATS = frozenset({"json", "sarif", "html", "junit", "markdown", "review"})


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
    """A ``compare`` result with a declared ``deployment:`` contract.

    Factored out so every format's disclosure test builds the identical
    fixture ``tests/test_report_schema.py``'s own
    ``test_env_matrix_source_sha256_validates_when_present`` already
    validates against the published JSON schema.
    """
    snap = _snapshot_with_glibc_dependency()
    matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
    result = compare(snap, snap, env_matrix=matrix)
    return result, snap


def _result_without_env_matrix():
    snap = _snapshot_with_glibc_dependency()
    return compare(snap, snap), snap


@pytest.mark.parametrize("fmt", sorted(_COMPARE_SUPPORTED_FORMATS))
def test_every_format_projects_the_env_matrix_digest_when_present(fmt: str) -> None:
    result, snap = _result_with_env_matrix()
    assert result.env_matrix_source_sha256 is not None, "fixture precondition"
    text = render_output(fmt, result, snap, snap)

    if fmt == "json":
        payload = json.loads(text)
        assert payload["env_matrix_source_sha256"] == result.env_matrix_source_sha256
    elif fmt == "sarif":
        payload = json.loads(text)
        props = payload["runs"][0]["properties"]
        assert props["envMatrixSourceSha256"] == result.env_matrix_source_sha256
    elif fmt == "junit":
        root = ET.fromstring(text)
        props = {
            p.get("name"): p.get("value")
            for p in root.findall("./testsuite/properties/property")
        }
        assert props["env_matrix_source_sha256"] == result.env_matrix_source_sha256
    elif fmt == "html":
        assert result.env_matrix_source_sha256 in text
    else:  # markdown, review
        assert result.env_matrix_source_sha256 in text, (
            f"{fmt} does not surface the declared-deployment-floor digest"
        )


@pytest.mark.parametrize("fmt", sorted(_COMPARE_SUPPORTED_FORMATS))
def test_every_format_omits_the_env_matrix_digest_when_absent(fmt: str) -> None:
    result, snap = _result_without_env_matrix()
    assert result.env_matrix_source_sha256 is None, "fixture precondition"
    text = render_output(fmt, result, snap, snap)

    if fmt == "json":
        payload = json.loads(text)
        assert "env_matrix_source_sha256" not in payload
    elif fmt == "sarif":
        payload = json.loads(text)
        assert "envMatrixSourceSha256" not in payload["runs"][0]["properties"]
    elif fmt == "junit":
        root = ET.fromstring(text)
        names = {
            p.get("name") for p in root.findall("./testsuite/properties/property")
        }
        assert "env_matrix_source_sha256" not in names
    elif fmt == "html":
        assert "Deployment floor digest" not in text
    else:  # markdown, review
        assert "Deployment floor digest" not in text
