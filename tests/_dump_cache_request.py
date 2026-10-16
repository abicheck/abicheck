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

"""Build a :class:`DumpCacheRequest` from keyword overrides, so cache tests can
state only the fields they care about."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from abicheck.workflows.dump.cache.api import DumpCacheRequest, _dump_is_cacheable


def dump_request(**overrides: Any) -> DumpCacheRequest:
    """A plain binary-only request; *overrides* replace any field."""
    fields: dict[str, Any] = dict(
        path=Path("/fixture/lib.so"),
        binary_fmt="elf",
        headers=None,
        includes=None,
        version="",
        lang="c++",
        lang_explicit=False,
        pdb_path=None,
        dwarf_only=False,
        debug_roots=None,
        enable_debuginfod=False,
        debuginfod_url=None,
        debug_format=None,
        symbols_only=False,
        debug_presence_only=False,
        public_headers=None,
        public_header_dirs=None,
        header_backend="auto",
        compile=None,
        include_labels=None,
        dump_manifest=None,
        include_dependencies=True,
        public_include_search_dirs=None,
    )
    fields.update(overrides)
    return DumpCacheRequest(**fields)


def dump_is_cacheable(**overrides: Any) -> bool:
    """The cacheability gate over keyword overrides of a plain request."""
    return _dump_is_cacheable(dump_request(**overrides))
