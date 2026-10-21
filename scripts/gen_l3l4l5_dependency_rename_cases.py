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

"""G31 Phase D fixture: a public entry's renamed internal dependency target.

Imported by ``gen_l3l4l5_examples.py``, which owns the write-or-``--check``
driver. Kept beside it, as ``gen_l3l4l5_dispatch_cases.py`` is, so that file
stays under its line cap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_Graph = Callable[..., dict[str, Any]]
_Node = Callable[..., Any]
_Edge = Callable[..., Any]


def dependency_rename_cases(
    graph: _Graph, node: _Node, edge: _Edge
) -> dict[str, tuple[str, dict[str, Any], dict[str, Any]]]:
    """case221: one rename and one unrelated addition in the same release.

    ``demo::Config``'s private field target ``RawConfig`` is renamed to
    ``RawConfigV2`` in the same header. A second public struct, ``demo::Other``,
    gains an unrelated private field ``Unrelated`` in the same release.

    Expected: exactly one ``public_api_internal_type_renamed`` (for ``Config``)
    and one ``public_api_internal_dependency_added`` (for ``Other``), with no
    ``declaration_renamed`` for the renamed pair -- the rename is reported once,
    and the unrelated add is not absorbed into it.
    """

    def _public(name: str, header: str) -> tuple[Any, Any]:
        return (
            node(
                f"type:{name}",
                "record_type",
                name,
                {"qualified_name": name, "visibility": "public_header"},
            ),
            node(f"hdr:include/demo/{header}", "header", f"include/demo/{header}"),
        )

    def _private(name: str) -> Any:
        return node(
            f"type:{name}",
            "record_type",
            name,
            {
                "qualified_name": name,
                "def_file": "include/demo/detail.h",
                "visibility": "private_header",
            },
        )

    config, config_hdr = _public("demo::Config", "config.h")
    other, other_hdr = _public("demo::Other", "other.h")
    raw_v1 = _private("demo::detail::RawConfig")
    raw_v2 = _private("demo::detail::RawConfigV2")
    unrelated = _private("demo::detail::Unrelated")

    def _decl(hdr: Any, entry: Any) -> Any:
        return edge(hdr.id, entry.id, "SOURCE_DECLARES")

    def _field(src: Any, dst: Any) -> Any:
        return edge(src.id, dst.id, "TYPE_HAS_FIELD_TYPE", attrs={"role": "field"})

    old = graph(
        [config, config_hdr, other, other_hdr, raw_v1],
        [_decl(config_hdr, config), _decl(other_hdr, other), _field(config, raw_v1)],
    )
    new = graph(
        [config, config_hdr, other, other_hdr, raw_v2, unrelated],
        [
            _decl(config_hdr, config),
            _decl(other_hdr, other),
            _field(config, raw_v2),
            _field(other, unrelated),
        ],
    )
    return {"case221_public_api_internal_type_renamed": ("L5", old, new)}
