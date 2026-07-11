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

"""The scan L2 include-seeding cleanup must run *locally* (before L3/L4 collection
replays its own inferred build query), not on the outer scan cleanup list.

Regression for a self-deadlock: the seed may run the inferred-CMake query and hold
its build dir under an exclusive flock until the cleanup runs. embed_build_source()
runs a second inferred query in the same call; deferring the seed cleanup to the
outer drain (which happens after embed) would make that second query block on the
still-held lock until the 600s timeout (Codex review)."""

from __future__ import annotations

from abicheck.cli_scan import _build_new_snapshot


def test_scan_l2_seed_cleanup_runs_before_embed(monkeypatch, tmp_path):
    events: list[str] = []
    seed_kwargs: dict = {}

    def fake_seed(**kwargs):
        seed_kwargs.update(kwargs)
        events.append("seed")
        # Faithful to the real seed_l2_includes: an inferred-CMake seed produces a
        # flock-release cleanup that, given a defer_cleanup list, is pushed there
        # (and returned as [] pending) — otherwise returned pending for the caller
        # to drain locally. So with the *bug* (outer list passed) the cleanup lands
        # on the outer list and never runs before embed; with the fix (None) it
        # comes back pending and the finally drains it first.
        cleanup = lambda: events.append("cleanup")  # noqa: E731
        defer = kwargs["defer_cleanup"]
        if defer is not None:
            defer.append(cleanup)
            return list(kwargs["includes"]), []
        return list(kwargs["includes"]), [cleanup]

    def fake_resolve(*args, **kwargs):
        events.append("resolve")
        return object()

    def fake_embed(*args, **kwargs):
        events.append("embed")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)
    monkeypatch.setattr("abicheck.cli_buildsource.embed_build_source", fake_embed)

    sources = tmp_path / "src"
    sources.mkdir()
    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[tmp_path / "h.h"],
        includes=[],
        sources=sources,
        collect_mode="build",  # non-"off" → embed_build_source runs
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],  # the outer scan list — the seed must NOT use it
    )

    # The seed was told to keep its cleanups local (not on the outer list), so the
    # flock releases in the finally before embed's own inferred query.
    assert seed_kwargs.get("defer_cleanup") is None
    # Ordering invariant: seed → resolve → cleanup (flock release) → embed.
    assert events == ["seed", "resolve", "cleanup", "embed"]
    assert events.index("cleanup") < events.index("embed")


def test_scan_returns_seeded_includes_for_baseline(monkeypatch, tmp_path):
    # _build_new_snapshot returns the *effective* (seeded) includes so a --baseline
    # compare can header-parse the old native library with the same build-derived
    # dependency include dirs (Codex review).
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    def fake_seed(**kwargs):
        return [seeded], []  # seed adds a build-derived dir, no cleanup

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr(
        "abicheck.service.resolve_input", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        "abicheck.cli_buildsource.embed_build_source", lambda *a, **k: None
    )

    snap, eff_includes = _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[tmp_path / "h.h"],
        includes=[],
        sources=tmp_path,
        collect_mode="build",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
    )
    assert seeded in eff_includes  # effective includes carry the seed for the baseline


def test_scan_l2_seed_cleanup_runs_even_when_resolve_raises(monkeypatch, tmp_path):
    # The flock must be released on the error path too (finally), so a failed L2
    # parse still can't wedge a later inferred query.
    events: list[str] = []

    def fake_seed(**kwargs):
        return list(kwargs["includes"]), [lambda: events.append("cleanup")]

    def fake_resolve(*args, **kwargs):
        from abicheck.errors import AbicheckError

        raise AbicheckError("boom")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)

    import click
    import pytest

    with pytest.raises(click.ClickException):
        _build_new_snapshot(
            binary=tmp_path / "lib.so",
            headers=[tmp_path / "h.h"],
            includes=[],
            sources=tmp_path,
            collect_mode="build",
            lang="c++",
            allow_build_query=False,
            defer_cleanup=[],
        )
    assert events == ["cleanup"]  # released despite the failure
