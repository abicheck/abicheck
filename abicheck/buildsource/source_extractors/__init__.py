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

"""Source ABI extractors (ADR-030 D3) — backends that fill a ``SourceAbiTu``.

One normalized contract (:class:`SourceAbiExtractor`, ADR-032) with several
backends behind it:

- :class:`CastxmlSourceExtractor` (phase 2) — declarations / types / public
  const-constexpr values; reuses the existing castxml parser.
- :class:`ClangSourceExtractor` (phase 5) — the *source-based* backend that adds
  inline/template/constexpr **body** fingerprints and default arguments via
  ``clang -ast-dump=json``. Requires clang; degrades to partial coverage if it
  is absent.
- :class:`AndroidHeaderAbiAdapter` (phase 6) — reuse Android header-checker
  ``.sdump``/``.lsdump`` dumps, normalized into the abicheck schema (ADR-030 D9).

The shared, tool-independent model→entity mapping lives in ``base``; the shared
compile-context → argv helpers live in ``_argv``.
"""

from __future__ import annotations

from .android import (
    ANDROID_EXTRACTOR_VERSION,
    AndroidHeaderAbiAdapter,
    parse_android_dump,
)
from .base import (
    SourceAbiExtractor,
    SourceExtractionError,
    assemble_source_tu,
)
from .castxml import (
    CASTXML_EXTRACTOR_VERSION,
    CastxmlSourceExtractor,
    build_castxml_command,
)
from .clang import (
    CLANG_EXTRACTOR_VERSION,
    ClangSourceExtractor,
    build_clang_command,
    build_clang_macro_command,
    macros_from_preprocessor,
    source_abi_from_clang_ast,
)
from .resolver import (
    ALL_CAPABILITIES,
    AUTO_PREFERENCE,
    PROFILES,
    SourceExtractorChoice,
    SourceExtractorProfile,
    resolve_source_extractor,
    select_source_backend,
)

__all__ = [
    "ALL_CAPABILITIES",
    "ANDROID_EXTRACTOR_VERSION",
    "AUTO_PREFERENCE",
    "CASTXML_EXTRACTOR_VERSION",
    "CLANG_EXTRACTOR_VERSION",
    "PROFILES",
    "AndroidHeaderAbiAdapter",
    "CastxmlSourceExtractor",
    "ClangSourceExtractor",
    "SourceAbiExtractor",
    "SourceExtractionError",
    "SourceExtractorChoice",
    "SourceExtractorProfile",
    "assemble_source_tu",
    "build_castxml_command",
    "build_clang_command",
    "build_clang_macro_command",
    "macros_from_preprocessor",
    "parse_android_dump",
    "resolve_source_extractor",
    "select_source_backend",
    "source_abi_from_clang_ast",
]
