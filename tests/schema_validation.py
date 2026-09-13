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

"""One prepared JSON-Schema validator per distinct schema, for the tests.

`jsonschema.validate(instance=..., schema=...)` re-validates the *schema*
itself on every call before it looks at the instance — the library's own
documentation says to reuse a validator once the schema is known good. The
suite validates many reports against a handful of packaged schemas, so that
per-call schema check is repeated work with no signal in it.

`validate_instance` keeps exactly the semantics of `jsonschema.validate` —
same `ValidationError` (the library's `best_match` choice, not merely the
first error encountered), same `format_checker` handling, same
`SchemaError` for a genuinely malformed schema — and differs only in
checking the schema once per distinct schema *content*.

The cache key is the schema's canonical JSON text, deliberately: keying on a
file path would serve a stale validator after a test edits a schema, and
keying on a dict's identity would do the same after a test mutates one in
place (both are things this suite really does — see
`tests/test_schema_validation_helper.py`, which states those as invariants).
"""

from __future__ import annotations

import copy
import importlib.util
import json
from typing import Any

import pytest

__all__ = [
    "jsonschema_available",
    "requires_jsonschema",
    "validate_instance",
    "validator_for",
]


def jsonschema_available() -> bool:
    """Whether the optional `jsonschema` dependency is installed."""
    return importlib.util.find_spec("jsonschema") is not None


#: The one spelling of "skip this structural-validation test when `jsonschema`
#: is absent". Each consumer used to carry its own four-line
#: `try: import jsonschema / except ImportError: jsonschema = None` preamble
#: purely to feed a `skipif`; that boilerplate has one owner now.
requires_jsonschema = pytest.mark.skipif(
    not jsonschema_available(), reason="jsonschema not installed"
)

_VALIDATORS: dict[tuple[str, int], Any] = {}


def validator_for(schema: Any, format_checker: Any = None) -> Any:
    """Return a validator for `schema`, checking the schema once per content.

    `format_checker` is part of the key: two validators over the same schema
    with and without one are not interchangeable.
    """
    import jsonschema  # imported lazily: an optional dependency several
    # suites skip cleanly without, so importing it at module scope would turn
    # their clean skip into a collection error.

    key = (json.dumps(schema, sort_keys=True, default=str), id(format_checker))
    validator = _VALIDATORS.get(key)
    if validator is None:
        # Deep-copy before handing the schema to the validator: a validator
        # keeps a reference to the mapping it was built from, so a caller that
        # later mutates its own schema in place would silently change what the
        # entry stored under the *old* content key enforces -- and the next
        # caller presenting an unmutated schema of that original content would
        # be served it. Keyed on content, so the cached copy must be the
        # content the key names, forever (Codex review, PR #1252).
        frozen = copy.deepcopy(schema)
        cls = jsonschema.validators.validator_for(frozen)
        cls.check_schema(frozen)  # raises SchemaError, exactly as validate() does
        validator = cls(frozen, format_checker=format_checker)
        _VALIDATORS[key] = validator
    return validator


def validate_instance(instance: Any, schema: Any, format_checker: Any = None) -> None:
    """Drop-in replacement for `jsonschema.validate(instance, schema)`."""
    from jsonschema.exceptions import best_match

    error = best_match(validator_for(schema, format_checker).iter_errors(instance))
    if error is not None:
        raise error
