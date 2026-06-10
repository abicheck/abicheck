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

"""Build-system evidence adapters (ADR-029 D3–D7).

Each adapter ingests one build-system surface and emits the shared, neutral
``BuildEvidence`` model. Adapters are post-build and non-executing by default
(ADR-028 D6): they read existing build outputs and pre-captured query output.
"""
from __future__ import annotations

from .base import (
    ABI_RELEVANT_FLAG_PREFIXES,
    BuildAdapter,
    compile_unit_id,
    detect_language,
    extract_abi_relevant_flags,
)
from .cmake_file_api import CMakeFileApiAdapter
from .compile_db import CompileDbAdapter
from .ninja import NinjaAdapter

__all__ = [
    "ABI_RELEVANT_FLAG_PREFIXES",
    "BuildAdapter",
    "CMakeFileApiAdapter",
    "CompileDbAdapter",
    "NinjaAdapter",
    "compile_unit_id",
    "detect_language",
    "extract_abi_relevant_flags",
]
