"""Coverage tests for abicheck/__init__.py — PackageNotFoundError fallback."""

from __future__ import annotations

import importlib


def test_version_fallback_when_not_installed(monkeypatch):
    """When the package is not installed, __version__ falls back to dev string."""
    from importlib.metadata import PackageNotFoundError

    import abicheck

    with monkeypatch.context() as m:
        m.setattr(
            "importlib.metadata.version",
            lambda _name: (_ for _ in ()).throw(PackageNotFoundError("abicheck")),
        )
        importlib.reload(abicheck)
        assert abicheck.__version__ == "0.0.0.dev0"

    # Patch is reverted — restore real version
    importlib.reload(abicheck)
