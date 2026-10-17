"""Unit tests for abicheck.extract.metadata_attach: NumPy C-API surface attach and swallowed metadata-attach failures."""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot

# ── try_attach_numpy_capi_surface() ────────────────────────────────────────


class TestTryAttachNumpyCapiSurface:
    def test_logs_only_when_consumption_detected(self, tmp_path, monkeypatch, caplog):
        # extract_numpy_capi_surface returns a real (non-None) surface with
        # False flags for an ordinary, successfully-scanned non-NumPy
        # library -- the INFO log must not fire for every such library
        # (CodeRabbit review).
        from abicheck.extract.metadata_attach import try_attach_numpy_capi_surface
        from abicheck.numpy_capi import NumPyCapiSurface

        snap = AbiSnapshot(library="lib.so", version="1.0")
        not_consuming = NumPyCapiSurface(
            consumes_array_api=False, consumes_ufunc_api=False
        )
        monkeypatch.setattr(
            "abicheck.numpy_capi.extract_numpy_capi_surface", lambda _p: not_consuming
        )
        with caplog.at_level("INFO", logger="abicheck.service"):
            try_attach_numpy_capi_surface(snap, tmp_path / "lib.so")
        assert snap.numpy_capi is not_consuming
        assert "NumPy C-API consumption detected" not in caplog.text

    @pytest.mark.parametrize(
        "consumes_array_api,consumes_ufunc_api",
        [(True, False), (False, True)],
    )
    def test_logs_when_consumption_detected(
        self, tmp_path, monkeypatch, caplog, consumes_array_api, consumes_ufunc_api
    ):
        # Parametrized over each side of the production OR condition, so
        # removing either one from try_attach_numpy_capi_surface's guard
        # would fail this test (CodeRabbit review).
        from abicheck.extract.metadata_attach import try_attach_numpy_capi_surface
        from abicheck.numpy_capi import NumPyCapiSurface

        snap = AbiSnapshot(library="lib.so", version="1.0")
        consuming = NumPyCapiSurface(
            consumes_array_api=consumes_array_api,
            consumes_ufunc_api=consumes_ufunc_api,
        )
        monkeypatch.setattr(
            "abicheck.numpy_capi.extract_numpy_capi_surface", lambda _p: consuming
        )
        with caplog.at_level("INFO", logger="abicheck.service"):
            try_attach_numpy_capi_surface(snap, tmp_path / "lib.so")
        assert snap.numpy_capi is consuming
        assert "NumPy C-API consumption detected" in caplog.text


class TestMetadataAttachFailuresAreSwallowed:
    """Each enrichment step must never fail a dump (ADR-037).

    The three ``except Exception`` handlers went untested while the block
    lived in ``service.py``; extracting it to ``extract.metadata_attach``
    surfaced that as uncovered new lines, so they are pinned here.
    """

    @staticmethod
    def _snap():
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(library="libfoo.so", version="1")

    def test_an_unresolvable_library_path_is_swallowed(self, caplog) -> None:
        # `resolve()` used to run *above* the handler, so a path it cannot
        # resolve aborted the whole dump instead of skipping metadata
        # extraction (CodeRabbit review).
        #
        # The failure is injected rather than staged from a real filesystem
        # shape, because every concrete trigger is either interpreter- or
        # environment-dependent: a symlink loop raises `RuntimeError` only
        # on 3.10-3.12 (3.13 switched to non-strict `realpath`; CI's 3.14
        # lane caught an earlier version of this test asserting otherwise),
        # and a deleted cwd raises everywhere but requires mutating process
        # state. What the fix is actually about is *where the call sits*, so
        # that is what this pins.
        import logging

        from abicheck.extract.metadata_attach import try_attach_sycl_metadata

        class _UnresolvablePath:
            def resolve(self):
                raise OSError("cannot resolve")

        snap = self._snap()
        with caplog.at_level(logging.DEBUG, logger="abicheck.service"):
            try_attach_sycl_metadata(snap, _UnresolvablePath())
        assert snap.sycl is None
        assert "SYCL metadata extraction skipped" in caplog.text

    def test_python_extension_detection_failure_attaches_nothing(
        self, monkeypatch, caplog
    ) -> None:
        import abicheck.python_ext as python_ext_mod
        from abicheck.extract.metadata_attach import try_attach_python_ext_metadata

        monkeypatch.setattr(
            python_ext_mod,
            "detect_python_extension",
            lambda _snap: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            try_attach_python_ext_metadata(snap)
        assert snap.python_ext is None
        assert "Python extension detection skipped" in caplog.text

    def test_numpy_capi_extraction_failure_attaches_nothing(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        import abicheck.numpy_capi as numpy_capi_mod
        from abicheck.extract.metadata_attach import try_attach_numpy_capi_surface

        monkeypatch.setattr(
            numpy_capi_mod,
            "extract_numpy_capi_surface",
            lambda _path: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            try_attach_numpy_capi_surface(snap, tmp_path / "libfoo.so")
        assert snap.numpy_capi is None
        assert "NumPy C-API surface extraction skipped" in caplog.text

    def test_python_api_recovery_failure_attaches_nothing(
        self, monkeypatch, caplog
    ) -> None:
        import abicheck.python_api as python_api_mod
        from abicheck.extract.metadata_attach import try_attach_python_api_surface

        monkeypatch.setattr(
            python_api_mod,
            "detect_python_api",
            lambda _snap: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            try_attach_python_api_surface(snap)
        assert snap.python_api is None
        assert "Python API surface recovery skipped" in caplog.text

    def test_sycl_metadata_is_attached_when_detected(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        import abicheck.sycl_metadata as sycl_mod
        from abicheck.extract.metadata_attach import try_attach_sycl_metadata

        detected = sycl_mod.SyclMetadata(implementation="dpcpp")
        monkeypatch.setattr(sycl_mod, "parse_sycl_metadata", lambda _dir: detected)
        snap = self._snap()
        with caplog.at_level("INFO", logger="abicheck.service"):
            try_attach_sycl_metadata(snap, tmp_path / "libfoo.so")
        assert snap.sycl is detected
        assert "SYCL metadata attached" in caplog.text

    def test_sycl_detection_failure_attaches_nothing(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        import abicheck.sycl_metadata as sycl_mod
        from abicheck.extract.metadata_attach import try_attach_sycl_metadata

        monkeypatch.setattr(
            sycl_mod,
            "parse_sycl_metadata",
            lambda _dir: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        snap = self._snap()
        with caplog.at_level("DEBUG", logger="abicheck.service"):
            try_attach_sycl_metadata(snap, tmp_path / "libfoo.so")
        assert snap.sycl is None
        assert "SYCL metadata extraction skipped" in caplog.text

    def test_a_non_sycl_library_attaches_nothing_quietly(
        self, tmp_path, monkeypatch
    ) -> None:
        import abicheck.sycl_metadata as sycl_mod
        from abicheck.extract.metadata_attach import try_attach_sycl_metadata

        monkeypatch.setattr(sycl_mod, "parse_sycl_metadata", lambda _dir: None)
        snap = self._snap()
        try_attach_sycl_metadata(snap, tmp_path / "libfoo.so")
        assert snap.sycl is None

    def test_a_library_with_no_numpy_capi_attaches_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        import abicheck.numpy_capi as numpy_capi_mod
        from abicheck.extract.metadata_attach import try_attach_numpy_capi_surface

        monkeypatch.setattr(
            numpy_capi_mod, "extract_numpy_capi_surface", lambda _path: None
        )
        snap = self._snap()
        try_attach_numpy_capi_surface(snap, tmp_path / "libfoo.so")
        assert snap.numpy_capi is None
