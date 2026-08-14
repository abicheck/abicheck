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

"""Raw-scalar YAML helpers for ``suppression.py``, split out to stay under
the file-size cap (CLAUDE.md). No dependency on ``suppression.py`` itself,
so the import direction stays one-way.
"""

from __future__ import annotations

import yaml


def raw_finding_ids_by_index(text: str) -> dict[int, str]:
    """``suppressions[]`` index -> raw, unresolved ``finding_id`` scalar
    text, if present.

    Codex review, PR #753 round 2: an unquoted all-octal-digit leading-zero
    scalar (``0123456701234567``) resolves to a *different* int
    (``5744368105847``) under PyYAML's YAML 1.1 resolver -- unrecoverable
    once ``yaml.safe_load`` has already thrown the original digits away, so
    coercing the parsed value with ``str()`` isn't enough. A loader-wide
    int-resolution ban is unsound the other way (this file's own
    ``version: 1`` needs a real int, and resolution has no per-key
    context) -- ``yaml.compose()``'s raw Node tree does: a
    ``ScalarNode.value`` is always the literal written text regardless of
    its resolved tag.
    """
    try:
        doc = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return {}
    if not isinstance(doc, yaml.MappingNode):
        return {}
    seq = next(
        (
            v
            for k, v in doc.value
            if isinstance(k, yaml.ScalarNode) and k.value == "suppressions"
        ),
        None,
    )
    if not isinstance(seq, yaml.SequenceNode):
        return {}
    raw_ids: dict[int, str] = {}
    for index, item_node in enumerate(seq.value):
        if not isinstance(item_node, yaml.MappingNode):
            continue
        for ik, iv in item_node.value:
            if (
                isinstance(ik, yaml.ScalarNode)
                and ik.value == "finding_id"
                and isinstance(iv, yaml.ScalarNode)
            ):
                raw_ids[index] = iv.value
    return raw_ids


def parse_finding_id(raw: object) -> str | None:
    """Coerce a ``finding_id`` value to ``str``. ``SuppressionList.load``
    passes the raw scalar text via :func:`raw_finding_ids_by_index`, so
    this only matters for a caller building ``Suppression`` directly with
    a non-``str`` value.
    """
    if raw is None:
        return None
    return str(raw)
