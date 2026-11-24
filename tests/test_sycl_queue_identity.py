# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""DPC++ queue type identity at the oneDAL host ABI boundary."""

from __future__ import annotations

import pytest

from abicheck.compare.naming_conventions import _has_sycl_queue_first_param
from abicheck.model import Function, Param


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("const sycl::_V1::queue &", True),
        ("cl::sycl::queue&", True),
        ("vendor::sycl::queue&", False),
        ("wrapper<sycl::queue>&", False),
        ("sycl::queue::native_handle_type", False),
    ],
)
def test_queue_identity(spelling: str, expected: bool) -> None:
    fn = Function(
        name="compute",
        mangled="compute",
        return_type="void",
        params=[Param(name="q", type=spelling)],
    )
    assert _has_sycl_queue_first_param(fn) is expected
