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

"""The one strict-YAML mapping loader every abicheck *manifest* format uses.

``yaml.safe_load`` alone silently accepts ``{a: 1, a: 2}`` with
last-value-wins semantics (PyYAML's ``SafeConstructor.construct_mapping``
never checks for a repeated key), so a manifest author who accidentally
repeats a field has part of their declared intent dropped with no signal at
all. Every hard-load-error manifest format in this repository closes that
gap the same standard way — a ``yaml.SafeLoader`` subclass overriding
``construct_mapping`` — and three independent copies of that override had
accumulated (``dump_manifest.py``, ``compatibility_evaluation_packs.py``,
``impact/use_cases.py``). They had already *diverged*: only two of the three
checked key hashability, so a sequence-keyed mapping (``? [a, b]\\n: 1``)
raised a raw ``TypeError`` out of one of them instead of that module's
documented error type, and each translated PyYAML's own implicit-resolver
``ValueError`` (an invalid timestamp-shaped scalar such as ``2023-99-99``)
differently or not at all.

This module owns the override once. :func:`load_strict_yaml` is the whole
supported surface: it normalizes *every* malformed-document failure —
syntax, duplicate key, unhashable key, out-of-range implicit scalar — into
the caller's own documented exception type, built by the ``error`` callback,
so each manifest format keeps its own error vocabulary without keeping its
own parser.

**Deliberately not used by** :mod:`abicheck.suppression_yaml`: a suppression
document's duplicate key is *not* an error there. That format resolves YAML
merge keys (``<<:``) and mirrors ``yaml.safe_load``'s own last-value-wins
mapping semantics on purpose, because its raw-node pass has to agree,
index for index, with the mapping ``safe_load`` actually built. Rejecting a
repeat there would change the format's accepted input, not just its
diagnostics — so it stays on the plain ``SafeLoader`` and is out of scope
for this consolidation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import yaml


class StrictMappingError(Exception):
    """A mapping this loader refuses: a duplicate or unhashable key.

    Raised from inside PyYAML's construction pass and caught by
    :func:`load_strict_yaml`, which re-raises it as the caller's own
    exception type. Deliberately *not* a ``yaml.YAMLError``: these are
    schema violations with a precise, one-line message of our own, and
    routing them through PyYAML's error vocabulary would bury that message
    in a multi-line ``ConstructorError`` context/mark dump.
    """


def _construct_strict_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """``SafeConstructor.construct_mapping`` plus two rejections.

    A **duplicate** key is the gap this loader exists to close. An
    **unhashable** key (a sequence or mapping used as a key, which PyYAML's
    own ``SafeConstructor`` already rejects) must be re-checked here rather
    than inherited, because this override replaces that constructor
    outright: without the check, ``key in mapping`` raises a bare
    ``TypeError`` that escapes every caller's documented error contract --
    which is exactly how the three copies of this override diverged.
    """
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        line = key_node.start_mark.line + 1
        try:
            hash(key)
        except TypeError as exc:
            raise StrictMappingError(
                f"unhashable mapping key {key!r} (line {line}): {exc}"
            ) from exc
        if key in mapping:
            raise StrictMappingError(
                f"duplicate key {key!r} in the same mapping (line {line})"
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


class StrictYamlLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` with :func:`_construct_strict_mapping` installed.

    A real ``SafeLoader`` subclass that only replaces the mapping
    constructor — it adds no tag, so it resolves and constructs exactly the
    safe scalar/sequence/mapping vocabulary ``yaml.safe_load`` does.
    """


StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_strict_mapping
)


def load_strict_yaml(text: str, *, error: Callable[[str], Exception]) -> Any:
    """Parse *text* as YAML under :class:`StrictYamlLoader`.

    Every malformed-document failure is raised as ``error(message)``:

    * a syntax error (``yaml.YAMLError``);
    * a duplicate mapping key, or an unhashable one (both
      :class:`StrictMappingError`, whose one-line message is passed through
      verbatim);
    * a bare ``ValueError`` from PyYAML's own implicit-resolver scalar
      constructors — a timestamp-shaped scalar with an out-of-range
      component (``2023-99-99``) is resolved to the timestamp tag by the
      *resolver*, before construction reaches this loader's override at all,
      and its built-in constructor raises a plain ``ValueError`` that is not
      a ``yaml.YAMLError``.

    A ``RecursionError`` from a pathologically nested document is
    deliberately **not** translated: it is a resource limit rather than a
    schema violation, and the one caller that surfaces it to a user
    (``workflows.bundle_facts_library_overrides``) needs its own message for
    it.
    """
    try:
        # `StrictYamlLoader` subclasses `yaml.SafeLoader` and only replaces
        # its mapping constructor; bandit's B506 flags every `Loader=` it
        # cannot name-match against `SafeLoader`/`CSafeLoader`, subclasses
        # included.
        return yaml.load(text, Loader=StrictYamlLoader)  # nosec B506
    except StrictMappingError as exc:
        raise error(str(exc)) from exc
    except yaml.YAMLError as exc:
        raise error(f"invalid YAML: {exc}") from exc
    except ValueError as exc:
        raise error(f"invalid YAML scalar: {exc}") from exc
