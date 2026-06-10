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
backends: castxml replay now (:class:`CastxmlSourceExtractor`), Clang LibTooling
and Android adapters later. The shared, tool-independent model→entity mapping
lives in ``base``.
"""

from __future__ import annotations

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

__all__ = [
    "CASTXML_EXTRACTOR_VERSION",
    "CastxmlSourceExtractor",
    "SourceAbiExtractor",
    "SourceExtractionError",
    "assemble_source_tu",
    "build_castxml_command",
]
