# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``gcc`` is a known, explicit header frontend (ADR-092) on every entry
point that names one, while genuinely unknown names stay refused."""

from __future__ import annotations

import pytest

from abicheck.buildsource.inline import BuildConfig
from abicheck.errors import ValidationError
from abicheck.extract.header_ast_backend import _resolve_header_backend

_UNKNOWN = ("edg", "msvc", "gcc-plugin", "gcc-experimental")


def test_resolver_accepts_gcc() -> None:
    assert _resolve_header_backend("gcc") == "gcc"
    assert _resolve_header_backend("GCC") == "gcc"


@pytest.mark.parametrize("name", _UNKNOWN)
def test_resolver_rejects_unknown_names(name: str) -> None:
    # "gcc-experimental" is the dump's producer stamp, not a selectable
    # frontend: the two names have different jobs and are not unified.
    with pytest.raises(ValidationError):
        _resolve_header_backend(name)


def test_build_config_accepts_gcc() -> None:
    assert (
        BuildConfig.from_dict({"compile": {"frontend": "gcc"}}).compile_frontend
        == "gcc"
    )


@pytest.mark.parametrize("name", _UNKNOWN)
def test_build_config_rejects_unknown_names(name: str) -> None:
    with pytest.raises(ValueError, match="compile.frontend"):
        BuildConfig.from_dict({"compile": {"frontend": name}})
