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

"""A consumer whose import table cannot be read is never "requires nothing".

Bug class ``evidence.silent_degradation_to_clean_verdict``
(``tests/regressions/manifest.py``). ``extract.consumer_imports`` records a
recognised binary whose ELF/PE/Mach-O import table fails part-way as a
``FAILED`` fact. Before this fix the workflow evaluated that fact's empty
requirement set anyway, so ``compare --used-by`` reported the consumer as
``NO_CHANGE`` with 100% symbol coverage -- a clean claim built on no evidence.
It now treats a ``FAILED`` read exactly like an unrecognised format: a
REQUIRED consumer raises ``ConsumerUnreadableError``, an ADVISORY one comes
back ``unreadable=True``.

The oracle is the extractor's own fact (``read_consumer_imports(...).status``),
checked independently of every workflow outcome asserted against it.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker_types import DiffResult
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.extract.consumer_imports import read_consumer_imports
from abicheck.model import AbiSnapshot
from abicheck.model.availability import FactStatus
from abicheck.model.consumer_spec import (
    ConsumerRequirement,
    ConsumerSpec,
    ConsumerUnreadableError,
)
from abicheck.policy.classification import Verdict
from abicheck.workflows.consumer_scope import (
    check_against,
    parse_app_requirements,
    scope_diff_to_app,
)

_ELF64_IDENT = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8

#: Five independently-built unreadable consumers across all three formats, each
#: failing at a different point of its parser.
_CORRUPT = {
    "elf_zero_body": b"\x7fELF" + b"\x00" * 100,
    "elf_bad_shentsize": _ELF64_IDENT
    + struct.pack("<HHIQQQIHHHHHH", 2, 62, 1, 0, 0, 10**6, 0, 64, 0, 64, 3, 0, 0),
    "pe_bad_e_lfanew": b"MZ" + b"\x90" * 200,
    "macho64_bad_load_command": struct.pack("<I", 0xFEEDFACF) + b"\x01" * 200,
    "macho32_truncated_header": struct.pack("<I", 0xFEEDFACE) + b"\x00" * 8,
}


def _snap(version: str, names: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version=version,
        elf=ElfMetadata(
            soname="libfoo.so.1", symbols=[ElfSymbol(name=n) for n in names]
        ),
    )


def _scope(consumer):
    return scope_diff_to_app(
        DiffResult(old_version="1", new_version="2", library="libfoo.so.1"),
        consumer,
        _snap("1.0", ["foo", "bar"]),
        _snap("2.0", ["foo"]),
    )


@pytest.fixture(params=sorted(_CORRUPT))
def corrupt_consumer(request, tmp_path) -> Path:
    path = tmp_path / request.param
    path.write_bytes(_CORRUPT[request.param])
    fact = read_consumer_imports(path, "libfoo.so.1")
    # Oracle sanity: each fixture is a *recognised* format whose read failed.
    assert fact.binary_format is not None
    assert fact.status is FactStatus.FAILED
    assert fact.failure_reason
    return path


class TestFailedReadIsUnreadable:
    def test_required_consumer_raises(self, corrupt_consumer):
        with pytest.raises(ConsumerUnreadableError):
            _scope(ConsumerSpec(path=corrupt_consumer))

    def test_bare_path_raises(self, corrupt_consumer):
        with pytest.raises(ConsumerUnreadableError):
            _scope(corrupt_consumer)

    def test_advisory_consumer_is_marked_unreadable_not_compatible(
        self, corrupt_consumer
    ):
        result = _scope(
            ConsumerSpec(
                path=corrupt_consumer, requirement=ConsumerRequirement.ADVISORY
            )
        )
        assert result.unreadable is True
        assert result.unreadable_reason
        # Independently of the flag: no coverage or requirement claim is made.
        assert result.symbol_coverage == 0.0
        assert result.required_symbol_count == 0
        assert result.verdict == Verdict.NO_CHANGE

    def test_parse_app_requirements_raises(self, corrupt_consumer):
        with pytest.raises(ConsumerUnreadableError):
            parse_app_requirements(corrupt_consumer, "libfoo.so.1")

    def test_check_against_raises(self, corrupt_consumer, tmp_path):
        with pytest.raises(ConsumerUnreadableError):
            check_against(corrupt_consumer, tmp_path / "missing-lib.so")


_magic = st.sampled_from(
    [b"\x7fELF", b"MZ", struct.pack("<I", 0xFEEDFACF), struct.pack("<I", 0xFEEDFACE)]
)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(magic=_magic, body=st.binary(max_size=256))
def test_a_failed_read_never_yields_a_result(tmp_path, magic, body):
    """For arbitrary bytes behind a recognised magic number: whenever the
    extractor reports FAILED, the workflow raises; whenever it reports
    PRESENT, the workflow returns a result. A FAILED fact never becomes a
    scoped result."""
    path = tmp_path / "consumer"
    path.write_bytes(magic + body)
    fact = read_consumer_imports(path, "libfoo.so.1")
    if fact.status is FactStatus.FAILED:
        with pytest.raises(ConsumerUnreadableError):
            _scope(path)
    else:
        assert _scope(path).unreadable is False


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_cli_rejects_a_consumer_with_an_unreadable_import_table(tmp_path):
    """End to end through ``compare --used-by``: a real consumer that needs a
    removed symbol is a scoped break; the same binary with its section-header
    table pointed past EOF is rejected instead of reading as compatible."""
    from click.testing import CliRunner

    from abicheck.cli import main

    def cc(*args):
        subprocess.run(["gcc", *args], check=True, cwd=tmp_path)

    (tmp_path / "v1.c").write_text(
        "int foo(void){return 1;}\nint bar(void){return 2;}\n"
    )
    (tmp_path / "v2.c").write_text("int foo(void){return 1;}\n")
    (tmp_path / "app.c").write_text("int bar(void);\nint main(void){return bar();}\n")
    cc("-shared", "-fPIC", "-Wl,-soname,libx.so.1", "-o", "old.so", "v1.c")
    cc("-shared", "-fPIC", "-Wl,-soname,libx.so.1", "-o", "new.so", "v2.c")
    cc("-o", "app", "app.c", "./old.so")
    data = bytearray((tmp_path / "app").read_bytes())
    struct.pack_into("<Q", data, 0x28, len(data) * 4)  # e_shoff past EOF
    (tmp_path / "app_bad").write_bytes(bytes(data))

    def run(app):
        return CliRunner().invoke(
            main,
            ["compare", str(tmp_path / "old.so"), str(tmp_path / "new.so"),
             "--used-by", str(tmp_path / app)],
        )  # fmt: skip

    good = run("app")
    assert good.exit_code == 4, good.output
    bad = run("app_bad")
    assert bad.exit_code != 0
    assert isinstance(bad.exception, ConsumerUnreadableError), bad.output
