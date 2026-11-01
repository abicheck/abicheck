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

"""A release pairs a member by the library it is when its file name cannot.

Known gap "Release fan-out cannot pair a stored snapshot directory with a
live one": pairing used only ``build_match_map``'s file-name key, so a
stored ``libfoo.json`` (a dump of ``libfoo.so.1``) and the live
``libfoo.so.1`` never paired and a stored/live directory compare compared
nothing (exit 1, ``no_comparison_completed``).

Two invariants, over every storage spelling of a stored member:

* **pairs**: a member the file name left unmatched on both sides pairs with
  the counterpart recording the same library, in either direction, for
  stored files and stored ``BundleFacts`` documents alike;
* **conservative**: a pair the file name made never moves, and a library
  claimed by two leftovers on one side, or already held by a pair, pairs
  nothing new.

The oracle is a hand-written table of library -> release key, not the
canonicalizer under test. The end-to-end directory and package routes are
``tests/test_release_member_identity_integration.py``.
"""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from abicheck.binary_utils import build_match_map
from abicheck.extract.member_identity import (
    _single_library_value,
    recorded_library_name,
)
from abicheck.model import AbiSnapshot
from abicheck.model.bundle_facts import BundleFacts
from abicheck.serialization import snapshot_to_json
from abicheck.storage.snapshot_codec import save_snapshot
from abicheck.workflows.release_member_pairing import (
    facts_paired_by_identity,
    identity_renames,
    pair_path_maps_by_identity,
    path_identity,
)

#: Recorded library -> the release key a live file of that name has.
#: Written out by hand: the oracle must not be the canonicalizer.
_LIBRARIES: dict[str, str] = {
    "libfoo.so": "libfoo.so",
    "libfoo.so.1": "libfoo.so",
    "libfoo.so.1.2.3": "libfoo.so",
    "Foo.dll": "foo.dll",
    "libfoo.1.dylib": "libfoo.dylib",
    "libfoo.dylib": "libfoo.dylib",
}

#: How a stored member's document may be named on disk.
_STORED_NAMES: dict[str, str] = {
    "unrelated_json": "dump-{digest}.json",
    "stem_json": "{stem}.json",
    "dump_default": "{lib}.abicheck.json",
    "gzip": "{lib}.abicheck.json.gz",
    "zstd": "{lib}.json.zst",
    "named_as_library": "{lib}",
}


def _stored(directory: Path, library: str, spelling: str) -> Path:
    stem = library.split(".", 1)[0]
    digest = hashlib.sha256(library.encode()).hexdigest()[:8]
    path = directory / _STORED_NAMES[spelling].format(
        lib=library, stem=stem, digest=digest
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    save_snapshot(AbiSnapshot(library=library, version="1"), path)
    return path


def _live_stub(directory: Path, name: str) -> Path:
    """A live member: a native-looking file whose identity is its name."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\x7fELF" + b"\0" * 60)
    return path


def _paired(old: dict[str, Path], new: dict[str, Path]) -> set[tuple[str, str]]:
    """The (OLD file, NEW file) pairs a key intersection makes after the
    identity pass."""
    new = pair_path_maps_by_identity(old, new)
    return {(old[k].name, new[k].name) for k in set(old) & set(new)}


@pytest.mark.parametrize(
    ("library", "spelling"), list(itertools.product(_LIBRARIES, _STORED_NAMES))
)
@pytest.mark.parametrize("stored_side", ["old", "new"])
def test_a_stored_member_pairs_with_the_live_library_it_records(
    tmp_path: Path, library: str, spelling: str, stored_side: str
) -> None:
    stored = _stored(tmp_path / "stored", library, spelling)
    live = _live_stub(tmp_path / "live", library)
    stored_map, _ = build_match_map([stored])
    live_map, _ = build_match_map([live])
    # The oracle table names the key a live file of that library has.
    assert set(live_map) == {_LIBRARIES[library]}

    old, new = (
        (stored_map, live_map) if stored_side == "old" else (live_map, stored_map)
    )
    expected = (
        {(stored.name, live.name)}
        if stored_side == "old"
        else {(live.name, stored.name)}
    )
    assert _paired(old, new) == expected


@pytest.mark.parametrize("spelling", list(_STORED_NAMES))
def test_a_stored_directory_pairs_every_member_of_the_live_one(
    tmp_path: Path, spelling: str
) -> None:
    libraries = ("libalpha.so.1", "libbeta.so.2.3", "libgamma.so")
    live = {lib: _live_stub(tmp_path / "live", lib) for lib in libraries}
    stored = {
        lib: _stored(tmp_path / "stored" / lib, lib, spelling) for lib in libraries
    }

    pairs = _paired(
        build_match_map(list(stored.values()))[0],
        build_match_map(list(live.values()))[0],
    )

    assert pairs == {(stored[lib].name, live[lib].name) for lib in libraries}


def test_a_file_name_pair_never_moves(tmp_path: Path) -> None:
    """Every document records ``libfoo.so``: the name-matched pair stays,
    and the leftovers pair nothing, because the library is already held."""
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for directory, names in (
        (old_dir, ("libfoo.json", "libremoved.json")),
        (new_dir, ("libfoo.json", "libadded.json")),
    ):
        directory.mkdir()
        for name in names:
            (directory / name).write_text(
                snapshot_to_json(AbiSnapshot(library="libfoo.so", version="1"))
            )
    old_map, _ = build_match_map(sorted(old_dir.iterdir()))
    new_map, _ = build_match_map(sorted(new_dir.iterdir()))

    assert _paired(old_map, new_map) == {("libfoo.json", "libfoo.json")}


def test_a_library_claimed_twice_on_one_side_pairs_nothing(tmp_path: Path) -> None:
    old = {
        name: _stored(tmp_path / "old" / name, "libfoo.so.1", "unrelated_json")
        for name in ("a", "b")
    }
    old_map = {f"{name}.json": path for name, path in old.items()}
    new_map, _ = build_match_map([_live_stub(tmp_path / "new", "libfoo.so.1")])

    assert _paired(old_map, new_map) == set()


def test_bundle_facts_documents_pair_by_recorded_library() -> None:
    """The stored/stored route: a document keyed by a dump's file name pairs
    with one keyed by the live name; a name-matched member is untouched."""

    def facts(members: dict[str, str]) -> BundleFacts:
        return BundleFacts(
            variant_fingerprint="v",
            per_library_snapshots={
                key: AbiSnapshot(library=lib, version="1")
                for key, lib in members.items()
            },
        )

    old = facts({"libalpha.json": "libalpha.so.1", "libgamma.so": "libgamma.so"})
    new = facts(
        {
            "libalpha.so": "libalpha.so.1",
            "libgamma.so": "libgamma.so",
            "libnew.so": "libnew.so",
        }
    )

    paired = facts_paired_by_identity(old, new)

    assert set(paired.per_library_snapshots) == {
        "libalpha.json",
        "libgamma.so",
        "libnew.so",
    }
    assert paired.per_library_snapshots["libalpha.json"].library == "libalpha.so.1"
    assert paired.library_filenames["libalpha.json"] == "libalpha.so"


def test_a_snapshot_recording_no_library_keys_by_its_file_name(tmp_path: Path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text(snapshot_to_json(AbiSnapshot(library="", version="1")))

    assert recorded_library_name(path) is None
    assert path_identity("plugin.json", path) == "plugin.json"


@pytest.mark.parametrize(
    "content",
    [b"\x7fELF" + b"\0" * 60, b"INPUT(libfoo.so.1)\n", b"plain text\n", b"{not json"],
    ids=["native", "linker_script", "text", "broken_json"],
)
def test_a_non_snapshot_member_is_its_own_key(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "libbar.so.3"
    path.write_bytes(content)

    assert recorded_library_name(path) is None
    assert path_identity("libbar.so", path) == "libbar.so"


def test_a_recorded_path_identifies_by_its_last_component(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(
        snapshot_to_json(AbiSnapshot(library="/usr/lib/libfoo.so.1", version="1"))
    )

    assert recorded_library_name(path) == "libfoo.so.1"
    assert path_identity("x.json", path) == "libfoo.so"


def test_an_unopenable_path_has_no_recorded_library(tmp_path: Path) -> None:
    """Never raises: a path that exists but cannot be read as a file (a
    directory; an unreadable file reaches the same `SnapshotError` branch
    of the compression probe) records no library and keys by its own name."""
    directory = tmp_path / "libdir.so"
    directory.mkdir()

    assert recorded_library_name(directory) is None
    assert path_identity("libdir.so", directory) == "libdir.so"


def _counting_identity(
    calls: list[str], identities: dict[str, str]
) -> Callable[[str], str]:
    def identity(key: str) -> str:
        calls.append(key)
        return identities.get(key, key)

    return identity


def test_nothing_is_probed_when_every_member_pairs_by_name() -> None:
    calls: list[str] = []
    probe = _counting_identity(calls, {})

    renames = identity_renames(
        ["liba.so", "libb.so"],
        ["liba.so", "libb.so"],
        old_identity=probe,
        new_identity=probe,
    )

    assert renames == {}
    assert calls == []


def test_paired_members_are_probed_only_for_a_matching_leftover_identity() -> None:
    """Leftovers whose identities never meet across the sides cost nothing
    on the name-paired members; a match costs one look at each of them."""
    calls: list[str] = []
    probe = _counting_identity(
        calls, {"x.json": "libx.so", "y.json": "liby.so", "z.json": "libz.so"}
    )

    no_match = identity_renames(
        ["paired.so", "x.json"],
        ["paired.so", "y.json"],
        old_identity=probe,
        new_identity=probe,
    )
    assert no_match == {}
    assert sorted(calls) == ["x.json", "y.json"]

    calls.clear()
    match = identity_renames(
        ["paired.so", "z.json"],
        ["paired.so", "libz.so"],
        old_identity=probe,
        new_identity=probe,
    )
    assert match == {"libz.so": "z.json"}
    assert "paired.so" in calls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '{"sections": {"provenance": {"payload": {"library": "libq.so.2"}}}}',
            "libq.so.2",
        ),
        # A nested `library` with another value makes the scan ambiguous.
        ('{"library": "libq.so.2", "sycl": [{"library": "libpi.so"}]}', None),
        # An empty or null recorded library is not an identity.
        ('{"library": "", "sycl": [{"library": "libpi.so"}]}', None),
        ('{"library": null, "sycl": [{"library": "libpi.so"}]}', None),
        # The key spelled inside a string value is not a member.
        (
            '{"note": "\\"library\\": \\"bogus.so\\"", "library": "libq.so.2"}',
            "libq.so.2",
        ),
        # A string value ending in an escaped quote before `library"`: the
        # key text matches at the escaped quote, which is not a member.
        ('{"k": "say \\"library", "library": "libq.so.2"}', "libq.so.2"),
    ],
    ids=[
        "single",
        "nested_other",
        "empty_plus_nested",
        "null_plus_nested",
        "inside_string",
        "escaped_quote_before_key",
    ],
)
def test_the_library_scan_answers_only_when_unambiguous(
    text: str, expected: str | None
) -> None:
    assert _single_library_value(text) == expected


def test_an_ambiguous_scan_falls_back_to_the_parsed_document(tmp_path: Path) -> None:
    """A document whose scan is ambiguous is parsed in full: the recorded
    top-level library still wins over a nested one."""
    path = tmp_path / "doc.json"
    document = json.loads(
        snapshot_to_json(AbiSnapshot(library="libq.so.2", version="1"))
    )
    document["zz_nested"] = {"library": "libpi.so"}
    path.write_text(json.dumps(document))

    assert recorded_library_name(path) == "libq.so.2"


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ('{"library": null}', "a single null library"),
        (
            '[{"library": "liba.so"}, {"library": "libb.so"}]',
            "an ambiguous non-object document",
        ),
        (
            '{"library": 7, "x": {"library": "libb.so"}}',
            "a non-string library after the parse",
        ),
        ('{"library": 7}', "a single non-string library"),
    ],
    ids=["null", "array", "non_string", "single_non_string"],
)
def test_a_document_without_a_string_library_records_none(
    tmp_path: Path, content: str, why: str
) -> None:
    path = tmp_path / "doc.json"
    if content.startswith("["):
        # Only a compressed document reaches the parse without a leading
        # `{`: the plain-text probe already rejects any other JSON value.
        path = tmp_path / "doc.json.gz"
        path.write_bytes(gzip.compress(content.encode()))
    else:
        path.write_text(content, encoding="utf-8")

    assert recorded_library_name(path) is None, why
    assert path_identity(path.name, path) == path.name


def test_a_member_that_cannot_be_stat_ed_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery listed it, so it exists; a `stat` that fails (permissions)
    makes it unreadable rather than aborting the run."""
    from abicheck.workflows.release_member_readability import (
        unreadable_unmatched_members,
    )

    locked = tmp_path / "liblocked.so"
    locked.write_bytes(b"\x7fELF" + b"\0" * 60)
    real_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        if self == locked:
            raise PermissionError(self)
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(
        "abicheck.extract.member_identity.is_content_readable_member",
        lambda path: path != locked,
    )

    unreadable = unreadable_unmatched_members({"liblocked.so": locked}, set())

    assert set(unreadable) == {"liblocked.so"}
    assert "liblocked.so" in unreadable["liblocked.so"]


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda d: d / "missing.so", False),
        (lambda d: (d / "dir.so").mkdir() or d / "dir.so", False),
        (
            lambda d: (
                (d / "libn.so").write_bytes(b"\x7fELF" + b"\0" * 60) and d / "libn.so"
            ),
            True,
        ),
        (lambda d: (d / "text.so").write_text("plain\n") and d / "text.so", False),
    ],
    ids=["missing", "directory", "elf_magic", "text"],
)
def test_content_recognition_needs_a_file_with_recognized_content(
    tmp_path: Path, make: Callable[[Path], Path], expected: bool
) -> None:
    from abicheck.classify import is_content_recognized_compare_input

    assert is_content_recognized_compare_input(make(tmp_path)) is expected
