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

"""Contract: every header-AST backend stamps its own producer label.

The dump labels a snapshot ``castxml`` or ``clang`` from the producer
recorded on the parser by ``_stamp_ast_parser``, not from the parser's
concrete class. This pins that each registered backend's parser carries
the producer equal to its registry key, so the label cannot drift from the
frontend that actually ran.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck import dumper
from abicheck.extract.headers.header_backend_protocol import HeaderParseRequest
from abicheck.extract.headers.toolchain import _parser_ast_toolchain

_HAVE_TOOL = {
    "castxml": shutil.which("castxml") is not None,
    "clang": shutil.which("clang") is not None,
}


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(dumper.HEADER_AST_BACKENDS))
def test_backend_parser_stamps_producer_equal_to_registry_key(
    name: str, tmp_path: Path
) -> None:
    if not _HAVE_TOOL.get(name, False):
        pytest.skip(f"{name} not installed")
    header = tmp_path / "contract.h"
    header.write_text("int contract_fn(int x);\n", encoding="utf-8")
    backend = dumper.HEADER_AST_BACKENDS[name]

    parser = backend.parse(
        HeaderParseRequest(headers=[header], extra_includes=[], lang="c")
    )

    assert _parser_ast_toolchain(parser)["producer"] == name
