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

"""Fake binary-format adapters for dump tests (lane B, stage B1b).

Inject a fake through the production seam -- the registry
``abicheck.workflows.dump.native.FORMAT_ADAPTERS`` -- instead of patching a
private extractor name::

    with fake_format_adapter("pe", result=snap) as fake:
        run_dump(path, "pe", headers=[h])
    assert fake.last.headers == [h]

``fake.requests`` holds every :class:`NativeExtractRequest` the dump made,
so a test asserts on what the orchestrator *asked* the format for -- the
same fields the real extractor receives as keyword arguments.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from abicheck.model import AbiSnapshot
from abicheck.workflows.dump.formats import NativeExtractRequest
from abicheck.workflows.dump.native import FORMAT_ADAPTERS


class FakeFormatAdapter:
    """Records each request; returns *result*, or ``side_effect(request)``.

    A *side_effect* that is an exception instance or class is raised instead.
    """

    def __init__(
        self,
        fmt: str,
        result: AbiSnapshot | None = None,
        *,
        side_effect: Callable[[NativeExtractRequest], AbiSnapshot]
        | BaseException
        | type[BaseException]
        | None = None,
    ) -> None:
        self.format = fmt
        self.result = result
        self.side_effect = side_effect
        self.requests: list[NativeExtractRequest] = []

    def extract(self, request: NativeExtractRequest) -> AbiSnapshot:
        self.requests.append(request)
        effect: Any = self.side_effect
        if isinstance(effect, BaseException) or (
            isinstance(effect, type) and issubclass(effect, BaseException)
        ):
            raise effect
        if effect is not None:
            return effect(request)
        if self.result is None:
            raise AssertionError(f"FakeFormatAdapter({self.format!r}) has no result")
        return self.result

    @property
    def last(self) -> NativeExtractRequest:
        assert self.requests, f"{self.format} adapter was never called"
        return self.requests[-1]

    @property
    def called(self) -> bool:
        return bool(self.requests)


@contextmanager
def fake_format_adapter(
    fmt: str,
    result: AbiSnapshot | None = None,
    *,
    side_effect: Any = None,
) -> Iterator[FakeFormatAdapter]:
    """Register one :class:`FakeFormatAdapter` for *fmt* for the block.

    The previous entry (or its absence) is restored on exit, even when the
    block raises.
    """
    fake = FakeFormatAdapter(fmt, result, side_effect=side_effect)
    missing = object()
    previous = FORMAT_ADAPTERS.get(fmt, missing)
    FORMAT_ADAPTERS[fmt] = fake  # type: ignore[assignment]
    try:
        yield fake
    finally:
        if previous is missing:
            del FORMAT_ADAPTERS[fmt]
        else:
            FORMAT_ADAPTERS[fmt] = previous  # type: ignore[assignment]
