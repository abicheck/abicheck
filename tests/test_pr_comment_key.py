# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`pr-comment-key` (issue #1708): per-job sticky PR comments.

Covers the renderer's keyed marker (Python) and the Action's lookup in
``action/run.sh`` (the real ``_sanitize_pr_comment_key``/``_pr_comment_marker``
/``_post_pr_comment`` functions, extracted verbatim and driven against an
in-memory ``gh`` stub that applies the real jq filter to a fake comment list).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash

from abicheck.pr_comment_base import CommentModel
from abicheck.pr_comment_render import (
    MARKER,
    render_comment,
    sanitize_comment_key,
    sticky_marker,
)

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_HOSTILE_KEYS = [
    "a-->b",
    '"; rm -rf / #',
    "x\ny",
    "<!-- z -->",
    "ü/ß lib",
    "--",
    ">" * 3,
    "k" * 200,
]


def _model() -> CommentModel:
    return CommentModel(
        mode="compare", subject="lib", old_label="o", new_label="n", policy="strict_abi"
    )


# --- renderer --------------------------------------------------------------


@pytest.mark.parametrize("key", [None, "", "   "])
def test_default_key_keeps_the_historical_marker(key) -> None:
    body = render_comment(_model(), sha="abc", comment_key=key)
    assert body.splitlines()[0] == "<!-- abicheck-sticky-report -->"
    assert body == render_comment(_model(), sha="abc")


def test_keyed_body_carries_only_its_own_marker() -> None:
    body = render_comment(_model(), sha="abc", comment_key="linux-x64")
    assert body.splitlines()[0] == "<!-- abicheck-sticky-report:linux-x64 -->"
    assert MARKER not in body


def test_distinct_keys_never_match_each_others_marker() -> None:
    keys = ["", "a", "ab", "a.b", "lib-1", "lib-10"]
    bodies = {k: render_comment(_model(), sha="s", comment_key=k) for k in keys}
    for k in keys:
        for other in keys:
            assert (sticky_marker(k) in bodies[other]) == (k == other), (k, other)


@pytest.mark.parametrize("key", _HOSTILE_KEYS)
def test_sanitized_key_cannot_close_the_comment(key: str) -> None:
    safe = sanitize_comment_key(key)
    assert len(safe) <= 64
    assert set(safe) <= set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    )
    marker = sticky_marker(key)
    # Exactly one comment terminator: the marker's own.
    assert marker.count("-->") == 1 and marker.endswith(" -->")
    assert marker.count(">") == 1 and '"' not in marker


# --- run.sh lookup ----------------------------------------------------------

_REGION_START = "_sanitize_pr_comment_key() {"
_REGION_END = "# ADR-067 D5:"

_HARNESS = r"""
_warning_annotation() { echo "WARN $*"; }
gh() {
  # gh api --paginate repos/R/issues/N/comments --jq FILTER
  # gh api -X PATCH repos/R/issues/comments/ID --input -
  # gh api -X POST  repos/R/issues/N/comments --input -
  if [[ "$2" == "--paginate" ]]; then
    jq -r "$5" "$STORE"
  elif [[ "$3" == "PATCH" ]]; then
    local id="${4##*/}" body; body=$(jq -r .body)
    jq --argjson id "$id" --arg b "$body" 'map(if .id == $id then .body = $b else . end)' \
      "$STORE" > "$STORE.tmp" && mv "$STORE.tmp" "$STORE"
  elif [[ "$3" == "POST" ]]; then
    local body; body=$(jq -r .body)
    jq --arg b "$body" '. + [{id: (length + 100), body: $b}]' "$STORE" > "$STORE.tmp" \
      && mv "$STORE.tmp" "$STORE"
  fi
}
"""


def _region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_REGION_START)
    return text[start : text.index(_REGION_END, start)]


def _bash(script: str, env: dict[str, str]) -> str:
    require_bash()
    if shutil.which("jq") is None:
        pytest.skip("jq not available")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run(
            [bash_executable(), path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, **env),
        )
    finally:
        os.unlink(path)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.parametrize("key", ["", "linux", "  spaced key ", *_HOSTILE_KEYS])
def test_run_sh_marker_matches_renderer(key: str) -> None:
    out = _bash(
        _region() + '\nprintf "%s" "$PR_COMMENT_MARKER"\n',
        {"INPUT_PR_COMMENT_KEY": key},
    )
    assert out == sticky_marker(key)


def _post(tmp_path: Path, store: list[dict], key: str, text: str) -> list[dict]:
    store_path = tmp_path / "store.json"
    store_path.write_text(json.dumps(store), encoding="utf-8")
    body_path = tmp_path / "body.md"
    body_path.write_text(
        render_comment(_model(), sha="s", comment_key=key) + text, encoding="utf-8"
    )
    _bash(
        _region() + _HARNESS + '\n_post_pr_comment 7 "$BODY"\n',
        {
            "INPUT_PR_COMMENT_KEY": key,
            "GITHUB_REPOSITORY": "o/r",
            "STORE": str(store_path),
            "BODY": str(body_path),
        },
    )
    return json.loads(store_path.read_text(encoding="utf-8"))


def test_run_sh_keys_keep_independent_sticky_comments(tmp_path: Path) -> None:
    store: list[dict] = [{"id": 1, "body": "unrelated human comment"}]
    store = _post(tmp_path, store, "", "run-default-1")
    store = _post(tmp_path, store, "a", "run-a-1")
    store = _post(tmp_path, store, "ab", "run-ab-1")
    assert len(store) == 4
    # Same key again: updates its own comment only.
    store = _post(tmp_path, store, "a", "run-a-2")
    store = _post(tmp_path, store, "", "run-default-2")
    assert len(store) == 4
    bodies = [c["body"] for c in store]
    assert bodies[0] == "unrelated human comment"
    assert bodies[1].startswith(MARKER) and bodies[1].endswith("run-default-2")
    assert bodies[2].startswith(sticky_marker("a")) and bodies[2].endswith("run-a-2")
    assert bodies[3].startswith(sticky_marker("ab")) and bodies[3].endswith("run-ab-1")
