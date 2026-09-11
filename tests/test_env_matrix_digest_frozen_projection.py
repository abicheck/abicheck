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

"""SARIF's and JUnit's ``env_matrix_source_sha256`` projections must read the
frozen ``ReportEnvelope``/``ReportDocument``, not the mutable ``DiffResult``.

Codex review, P2 (fresh evidence after the prior all-formats fix,
``tests/test_compare_env_matrix_digest_formats.py``): that round proved every
format *projects* the digest, but never proved a format reads it from the
right place. ``report/AGENTS.md``'s ADR-061 Phase 2 gap C contract is "one
immutable report, multiple pure projections" -- once a
:class:`~abicheck.report.envelope.ReportEnvelope` is built, every later
projection of that same envelope must agree, even if the caller goes on to
mutate the ``DiffResult`` it wraps. ``sarif.py``'s SARIF ``properties`` block
and ``junit_report.py``'s ``_add_env_matrix_property`` were still reading
``result.env_matrix_source_sha256`` straight off the (mutable) ``DiffResult``
instead of ``resolved_document(envelope, report_document)``, so a mutation
after the envelope was built leaked into these two formats while JSON/
Markdown/HTML (via their own already-fixed ``build_report_document``/
``build_html_document`` choke points) did not regress.

This mirrors the structure of the pre-existing
``report/disposition_audit.disposition_audit_dict_reusing_document`` reuse
(the "prior all-formats fix" for that field) and its own frozen-document
posture: build one envelope, mutate the *result* the envelope wraps, render,
and assert the render reflects the value as of envelope build time.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.junit_report import to_junit_xml
from abicheck.model import AbiSnapshot
from abicheck.report.build import build_report_envelope
from abicheck.sarif import to_sarif_str


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


def _envelope_with_env_matrix() -> tuple[object, AbiSnapshot]:
    """A built envelope wrapping a ``compare`` result with a declared
    ``deployment:`` contract -- same fixture shape as
    ``tests/test_compare_env_matrix_digest_formats.py``'s own
    ``_result_with_env_matrix``, but resolved through
    :func:`build_report_envelope` so the test can mutate the envelope's own
    copy of the result *after* the envelope (and its shared document) already
    froze the digest.
    """
    snap = _snapshot_with_glibc_dependency()
    matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
    result = compare(snap, snap, env_matrix=matrix)
    assert result.env_matrix_source_sha256 is not None, "fixture precondition"
    envelope = build_report_envelope(result, snap, snap)
    return envelope, snap


class TestSarifEnvMatrixDigestFrozen:
    def test_sarif_reads_the_envelope_digest_not_a_later_mutation(self) -> None:
        envelope, snap = _envelope_with_env_matrix()
        original_digest = envelope.result.env_matrix_source_sha256

        # Mutate the envelope's own copy of the result *after* the envelope
        # (and its shared document) already resolved -- per
        # `build_report_envelope`'s own docstring, this is exactly the copy
        # every projection reads via `envelope.result`.
        envelope.result.env_matrix_source_sha256 = "mutated-after-envelope-build"

        text = to_sarif_str(
            envelope.result,
            show_only=envelope.options.show_only,
            report_mode=envelope.options.report_mode,
            severity_config=envelope.severity_config,
            envelope=envelope,
        )

        import json

        payload = json.loads(text)
        digest = payload["runs"][0]["properties"]["envMatrixSourceSha256"]
        assert digest == original_digest
        assert digest != "mutated-after-envelope-build"

    def test_sarif_with_no_envelope_keeps_reading_result_directly(self) -> None:
        """A direct caller with neither envelope nor report_document keeps
        the prior, independent behaviour (mirrors
        ``disposition_audit_dict_reusing_document``'s own documented
        fallback)."""
        snap = _snapshot_with_glibc_dependency()
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        result = compare(snap, snap, env_matrix=matrix)
        result.env_matrix_source_sha256 = "direct-caller-value"

        import json

        payload = json.loads(to_sarif_str(result))
        assert (
            payload["runs"][0]["properties"]["envMatrixSourceSha256"]
            == "direct-caller-value"
        )


class TestJUnitEnvMatrixDigestFrozen:
    def test_junit_reads_the_envelope_digest_not_a_later_mutation(self) -> None:
        envelope, snap = _envelope_with_env_matrix()
        original_digest = envelope.result.env_matrix_source_sha256

        envelope.result.env_matrix_source_sha256 = "mutated-after-envelope-build"

        text = to_junit_xml(
            envelope.result,
            snap,
            show_only=envelope.options.show_only,
            severity_config=envelope.severity_config,
            report_mode=envelope.options.report_mode,
            envelope=envelope,
        )

        root = ET.fromstring(text)
        props = {
            p.get("name"): p.get("value")
            for p in root.findall("./testsuite/properties/property")
        }
        assert props["env_matrix_source_sha256"] == original_digest
        assert props["env_matrix_source_sha256"] != "mutated-after-envelope-build"

    def test_junit_with_no_envelope_keeps_reading_result_directly(self) -> None:
        snap = _snapshot_with_glibc_dependency()
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        result = compare(snap, snap, env_matrix=matrix)
        result.env_matrix_source_sha256 = "direct-caller-value"

        text = to_junit_xml(result, snap)
        root = ET.fromstring(text)
        props = {
            p.get("name"): p.get("value")
            for p in root.findall("./testsuite/properties/property")
        }
        assert props["env_matrix_source_sha256"] == "direct-caller-value"
