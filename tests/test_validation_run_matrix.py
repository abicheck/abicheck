"""Tests for the real-world validation harness helpers.

``logical_name`` now lives in the shared engine (``conda_harness``) that both
``run_matrix.py`` and ``validate.py`` build on; these cases pin its behaviour
on sonames the curated manifest relies on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path("validation/scripts/conda_harness.py")


def _load_logical_name():
    spec = importlib.util.spec_from_file_location("conda_harness", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.logical_name


def test_logical_name_handles_standard_soname_suffixes() -> None:
    logical_name = _load_logical_name()

    assert logical_name("/pkg/lib/libprotobuf.so.33.5.0") == "libprotobuf"
    assert logical_name("/pkg/lib/libssl.so.3") == "libssl"


def test_logical_name_strips_version_embedded_before_so_suffix() -> None:
    logical_name = _load_logical_name()

    assert logical_name("/pkg/lib/libcapnp-1.4.0.so") == "libcapnp"
    assert logical_name("/pkg/lib/libkj-async-1.3.0.so") == "libkj-async"
