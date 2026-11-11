# SPDX-License-Identifier: Apache-2.0
"""Concrete header-AST producers; dispatch and tests share this registry."""

from .backend_protocol import HeaderAstBackend
from .castxml.backend import CastxmlBackend
from .clang.backend import ClangBackend
from .gcc.backend import GccBackend

HEADER_AST_BACKENDS: dict[str, HeaderAstBackend] = {
    "castxml": CastxmlBackend(),
    "clang": ClangBackend(),
    "gcc": GccBackend(),
}
