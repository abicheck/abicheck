"""The castxml header-AST cache as a view over the per-invocation parse store (Phase 6 slice 2).

Two properties carry the slice:

* **Cache-hit equals cold run.** A warm castxml dump returns the same tree, field
  for field, that the cold run parsed, and the warm run never runs castxml.
* **Key sensitivity.** The castxml key changes when any input the parse reads
  changes -- a header's content, an include root's content, a flag -- and does
  not change for a touch that leaves every content digest the same.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from xml.etree.ElementTree import Element, tostring

import pytest

from abicheck.extract.headers.ast_config import _cache_key
from abicheck.extract.headers.castxml import backend as _cx
from abicheck.extract.headers.castxml.backend import castxml_dump as _castxml_dump
from abicheck.storage.tu_parse_store import TuParseStore

_CASTXML_DOC = (
    '<GCC_XML><File id="f1" name="api.hpp"/>'
    '<Function id="_1" name="api" returns="_2"/></GCC_XML>'
)


def _fake_castxml(counter: list[int]):
    """A castxml stand-in that writes a fixed document to its output path."""

    def run(*args, **kwargs):
        for arg in args:
            if isinstance(arg, list):
                for part in arg:
                    if str(part).endswith(".xml") and "castxml" not in str(part):
                        counter.append(1)  # a parse, not a version or feature probe
                        Path(part).write_text(_CASTXML_DOC, encoding="utf-8")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    return run


def _use_store(monkeypatch: pytest.MonkeyPatch, store: TuParseStore) -> None:
    monkeypatch.setattr(_cx, "_resolve_selected_tool", lambda _: "/mock/castxml")
    monkeypatch.setattr(_cx, "ast_cache_store", lambda key, backend="castxml": store)


def _signature(root: Element) -> str:
    return tostring(root, encoding="unicode")


class TestWarmHitEqualsColdRun:
    def test_warm_hit_returns_the_cold_tree_and_skips_castxml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        header = tmp_path / "api.hpp"
        header.write_text("int api();\n", encoding="utf-8")
        store = TuParseStore(tmp_path / "store")
        _use_store(monkeypatch, store)

        runs: list[int] = []
        cold = _cx.castxml_dump([header], [], lang="c", run=_fake_castxml(runs))
        warm = _cx.castxml_dump([header], [], lang="c", run=_fake_castxml(runs))

        assert len(runs) == 1, "the warm dump must not run castxml again"
        assert _signature(warm) == _signature(cold)
        assert warm.find("Function").get("name") == "api"

    def test_entry_is_a_parse_store_entry_under_the_castxml_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        header = tmp_path / "api.hpp"
        header.write_text("int api();\n", encoding="utf-8")
        store = TuParseStore(tmp_path / "store")
        _use_store(monkeypatch, store)

        _cx.castxml_dump([header], [], lang="c", run=_fake_castxml([]))

        entries = sorted(p.name for p in (tmp_path / "store").iterdir())
        assert len(entries) == 1 and entries[0].endswith(".json")
        key = entries[0][: -len(".json")]
        assert store.lookup(key) == _CASTXML_DOC


class TestCacheKeySensitivity:
    @pytest.fixture
    def tree(self, tmp_path: Path) -> dict[str, Path]:
        header = tmp_path / "api.hpp"
        header.write_text("int api();\n", encoding="utf-8")
        root = tmp_path / "include"
        root.mkdir()
        (root / "dep.h").write_text("#define DEP 1\n", encoding="utf-8")
        return {"header": header, "root": root}

    @staticmethod
    def _key(tree: dict[str, Path], **overrides: object) -> str:
        kwargs: dict[str, object] = {
            "compiler": "cc",
            "lang": "c",
            "frontend_identity": "castxml-1",
            "compiler_identity": "cc-1",
            "invocation_tool": ("cc", "gnu", "castxml"),
        }
        kwargs.update(overrides)
        extra = kwargs.pop("extra_includes", [tree["root"]])
        headers = kwargs.pop("headers", [tree["header"]])
        return _cache_key(headers, extra, backend="castxml", **kwargs)  # type: ignore[arg-type]

    def test_key_is_stable_for_identical_inputs(self, tree) -> None:
        assert self._key(tree) == self._key(tree)

    def test_touching_a_header_without_editing_it_keeps_the_key(self, tree) -> None:
        before = self._key(tree)
        header = tree["header"]
        header.touch()
        assert self._key(tree) == before, "content digests, not mtimes, key the entry"

    def test_editing_a_header_changes_the_key(self, tree) -> None:
        before = self._key(tree)
        tree["header"].write_text("int api(int);\n", encoding="utf-8")
        assert self._key(tree) != before

    def test_editing_a_file_under_an_include_root_changes_the_key(self, tree) -> None:
        before = self._key(tree)
        (tree["root"] / "dep.h").write_text("#define DEP 2\n", encoding="utf-8")
        assert self._key(tree) != before

    def test_adding_a_file_under_an_include_root_changes_the_key(self, tree) -> None:
        before = self._key(tree)
        (tree["root"] / "new.h").write_text("#define NEW 1\n", encoding="utf-8")
        assert self._key(tree) != before

    @pytest.mark.parametrize(
        "override",
        [
            {"lang": "c++"},
            {"force_cpp": True},
            {"force_cpp20": True},
            {"sysroot": Path("/opt/sysroot")},
            {"nostdinc": True},
            {"gcc_options": "-m32"},
            {"gcc_option_tokens": ("-m32",)},
            {"gcc_path": "/opt/gcc/bin/gcc"},
            {"gcc_prefix": "arm-none-eabi-"},
            {"system_includes": ("/usr/include",)},
            {"compiler": "c++"},
            {"frontend_identity": "castxml-2"},
            {"compiler_identity": "cc-2"},
            {"invocation_tool": ("cc", "gnu", "castxml-other")},
        ],
    )
    def test_each_parse_input_changes_the_key(self, tree, override) -> None:
        assert self._key(tree, **override) != self._key(tree)

    def test_a_missing_include_root_is_keyed_apart_from_a_present_one(
        self, tree
    ) -> None:
        present = self._key(tree)
        assert self._key(tree, extra_includes=[tree["root"] / "absent"]) != present


class TestStoredEntryEviction:
    def test_unusable_payload_is_evicted_and_misses(self, tmp_path: Path) -> None:
        store = TuParseStore(tmp_path / "store")
        store.publish("k", "<GCC_XML><unclosed>", ())

        assert _cx._read_castxml_cache(store, "k") is None
        assert not store.path("k").exists()

    def test_a_usable_payload_parses_to_its_document(self, tmp_path: Path) -> None:
        store = TuParseStore(tmp_path / "store")
        store.publish("k", _CASTXML_DOC, ())

        root = _cx._read_castxml_cache(store, "k")
        assert root is not None
        assert root.find("Function").get("name") == "api"


def _seeded_store(xml_file: Path) -> TuParseStore:
    """A parse store whose ``testkey`` entry holds the castxml XML in *xml_file*."""
    store = TuParseStore(xml_file.parent / "store")
    store.publish("testkey", xml_file.read_text(encoding="utf-8"), ())
    return store


class TestCastxmlCacheBehaviour:
    def test_cache_hit_returns_cached(self, tmp_path, monkeypatch):
        """When cache file exists, castxml is not invoked."""
        monkeypatch.setattr(_cx, "_resolve_selected_tool", lambda _: "/mock/castxml")
        # Create a valid XML cache file
        cache_xml = tmp_path / "cached.xml"
        root = Element("GCC_XML")
        from xml.etree.ElementTree import ElementTree

        ElementTree(root).write(str(cache_xml))

        # Patch _cache_key/_cache_path to return our cached file
        monkeypatch.setattr(_cx, "_cache_key", lambda *a, **kw: "testkey")
        monkeypatch.setattr(
            _cx,
            "ast_cache_store",
            lambda k, backend="castxml": _seeded_store(cache_xml),
        )

        result = _castxml_dump([Path("h.h")], [])
        assert result.tag == "GCC_XML"

    def test_cache_hit_rechecks_deadline(self, tmp_path, monkeypatch):
        """Codex review (PR #591): a warm XML cache hit still costs real time
        parsing a potentially large cached AST — deadline.check() must fire
        on that path too, not just once before castxml would be spawned."""
        monkeypatch.setattr(_cx, "_resolve_selected_tool", lambda _: "/mock/castxml")
        cache_xml = tmp_path / "cached.xml"
        from xml.etree.ElementTree import ElementTree

        ElementTree(Element("GCC_XML")).write(str(cache_xml))
        monkeypatch.setattr(_cx, "_cache_key", lambda *a, **kw: "testkey")
        monkeypatch.setattr(
            _cx,
            "ast_cache_store",
            lambda k, backend="castxml": _seeded_store(cache_xml),
        )

        from abicheck import deadline

        with deadline.deadline_scope(-1):  # already expired
            with pytest.raises(deadline.DeadlineExceeded):
                _castxml_dump([Path("h.h")], [])

    def test_cache_hit_rechecks_deadline_after_parse(self, tmp_path, monkeypatch):
        """Codex review (PR #591, round 3): DefusedET.parse() on a warm cache
        hit can itself consume the rest of the budget for a huge cached XML
        tree -- the existing pre-parse deadline.check() doesn't catch that;
        must re-check again after the parse before handing the root off."""
        import time

        monkeypatch.setattr(_cx, "_resolve_selected_tool", lambda _: "/mock/castxml")
        cache_xml = tmp_path / "cached.xml"
        from xml.etree.ElementTree import ElementTree

        ElementTree(Element("GCC_XML")).write(str(cache_xml))
        monkeypatch.setattr(_cx, "_cache_key", lambda *a, **kw: "testkey")
        monkeypatch.setattr(
            _cx,
            "ast_cache_store",
            lambda k, backend="castxml": _seeded_store(cache_xml),
        )

        from abicheck import deadline
        from abicheck.storage.castxml_xml import parse_castxml_text

        # The warm-cache parse this exercises is `_read_castxml_cache`'s parse of
        # the stored payload, so slow down the parser the castxml backend holds.
        def _slow_parse(text):
            time.sleep(0.05)
            return parse_castxml_text(text)

        monkeypatch.setattr(_cx, "parse_castxml_text", _slow_parse)
        with deadline.deadline_scope(0.03):
            with pytest.raises(deadline.DeadlineExceeded):
                _castxml_dump([Path("h.h")], [])

    def test_fresh_run_rechecks_deadline_after_writing_cache(
        self, tmp_path, monkeypatch
    ):
        """Codex review (PR #591, round 10): after a fresh (non-cached)
        castxml run parses successfully, castxml_dump() still re-reads the
        whole output file (read_bytes) and writes it to the AST cache
        (_atomic_write) before returning the parsed root -- that read+write
        can itself consume real time on a huge fresh XML tree, but nothing
        re-checked the deadline after it. Must re-check before returning."""
        import subprocess
        import time
        from xml.etree.ElementTree import Element as _Element, ElementTree

        monkeypatch.setattr(_cx, "_resolve_selected_tool", lambda _: "/mock/castxml")
        monkeypatch.setattr(_cx, "_cache_key", lambda *a, **kw: "k")
        monkeypatch.setattr(
            _cx,
            "ast_cache_store",
            lambda k, backend="castxml": TuParseStore(tmp_path / "c"),
        )

        def fake_run(*args, **kwargs):
            for a in args:
                if isinstance(a, list):
                    for part in a:
                        if str(part).endswith(".xml") and "castxml" not in str(part):
                            root = _Element("GCC_XML")
                            root.append(_Element("File", id="f1", name="foo.h"))
                            ElementTree(root).write(str(part))
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        def _slow_publish(self, key, payload, dep_paths, memo=None):
            time.sleep(0.05)

        monkeypatch.setattr(TuParseStore, "publish", _slow_publish)

        from abicheck import deadline

        with deadline.deadline_scope(0.03):
            with pytest.raises(deadline.DeadlineExceeded):
                _castxml_dump([Path("h.h")], [], run=fake_run)

    def test_corrupt_cache_is_discarded(self, tmp_path, monkeypatch):
        """Corrupt cache entry is removed before castxml is re-invoked."""
        import subprocess

        monkeypatch.setattr(_cx, "_resolve_selected_tool", lambda _: "/mock/castxml")
        # An unparseable (empty) payload under the entry's key
        store = TuParseStore(tmp_path / "store")
        store.publish("testkey", "", ())

        monkeypatch.setattr(_cx, "_cache_key", lambda *a, **kw: "testkey")
        monkeypatch.setattr(_cx, "ast_cache_store", lambda k, backend="castxml": store)

        # Track whether cache was already gone when the injected runner was called
        cache_existed_at_run = []

        def fake_run(*args, **kwargs):
            cache_existed_at_run.append(store.path("testkey").exists())
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="castxml stub error"
            )

        with pytest.raises(RuntimeError, match="castxml failed"):
            _castxml_dump([Path("h.h")], [], run=fake_run)

        # subprocess.run must have been called (cache didn't short-circuit)
        assert cache_existed_at_run, "subprocess.run was never called"
        # The corrupt cache must have been deleted BEFORE the re-run
        assert not cache_existed_at_run[0], (
            "Cache was not deleted before castxml re-run"
        )
        # And must still be gone after
        assert not store.path("testkey").exists()


class TestUnreadableInputsKeepTheKey:
    def test_dangling_header_symlink_under_a_root_keys_and_changes_when_readable(
        self, tmp_path: Path
    ) -> None:
        header = tmp_path / "api.hpp"
        header.write_text("int api();\n", encoding="utf-8")
        root = tmp_path / "include"
        root.mkdir()
        target = tmp_path / "generated.h"
        (root / "dangling.h").symlink_to(target)

        dangling = _cache_key([header], [root], "cc", backend="castxml", lang="c")
        empty = _cache_key([header], [tmp_path / "other"], "cc", backend="castxml")
        target.write_text("#define GENERATED 1\n", encoding="utf-8")
        readable = _cache_key([header], [root], "cc", backend="castxml", lang="c")

        assert len({dangling, empty, readable}) == 3


class TestBestEffortCacheCleanup:
    def test_a_failed_eviction_leaves_the_entry_and_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TuParseStore(tmp_path / "store")
        store.publish("k", "payload", ())

        def refuse(self: Path, missing_ok: bool = False) -> None:
            raise PermissionError("read-only cache")

        monkeypatch.setattr(Path, "unlink", refuse)
        store.evict("k")

        monkeypatch.undo()
        assert store.lookup("k") == "payload"

    def test_a_failed_publish_does_not_raise_and_leaves_no_entry(
        self, tmp_path: Path
    ) -> None:
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("", encoding="utf-8")
        store = TuParseStore(blocked)

        store.publish("k", "payload", ())

        assert store.lookup("k") is None


class TestWriteAndStoreBoundaries:
    def test_a_matching_toolchain_publishes_the_dumped_xml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TuParseStore(tmp_path / "store")
        out = tmp_path / "out.xml"
        out.write_text(_CASTXML_DOC, encoding="utf-8")
        monkeypatch.setattr(_cx, "_tool_identity", lambda _b: "same")

        _cx._write_castxml_cache(
            store,
            "k",
            out,
            castxml_bin="castxml",
            cc_bin="cc",
            frontend_identity="same",
            compiler_identity="same",
        )

        assert store.lookup("k") == _CASTXML_DOC

    def test_a_toolchain_that_moved_during_the_run_publishes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TuParseStore(tmp_path / "store")
        out = tmp_path / "out.xml"
        out.write_text(_CASTXML_DOC, encoding="utf-8")
        monkeypatch.setattr(_cx, "_tool_identity", lambda _b: "upgraded")

        _cx._write_castxml_cache(
            store,
            "k",
            out,
            castxml_bin="castxml",
            cc_bin="cc",
            frontend_identity="before",
            compiler_identity="before",
        )

        assert store.lookup("k") is None

    def test_an_unreadable_dump_publishes_nothing_and_raises_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TuParseStore(tmp_path / "store")
        monkeypatch.setattr(_cx, "_tool_identity", lambda _b: "same")

        _cx._write_castxml_cache(
            store,
            "k",
            tmp_path / "never-written.xml",
            castxml_bin="castxml",
            cc_bin="cc",
            frontend_identity="same",
            compiler_identity="same",
        )

        assert store.lookup("k") is None

    def test_the_real_store_for_a_key_is_the_backend_entry_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.storage import ast_cache_location as alc

        monkeypatch.setattr(alc.AST_DISK_CACHE, "enabled", lambda: True)
        monkeypatch.setattr(
            alc, "backend_cache_dir", lambda backend: tmp_path / backend
        )

        store = alc.ast_cache_store("k", "castxml")

        assert store.path("k") == tmp_path / "castxml" / "k.json"


def test_a_disabled_cache_still_names_the_entry_json_in_scratch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck.storage import ast_cache_location as alc

    monkeypatch.setattr(alc.AST_DISK_CACHE, "enabled", lambda: False)

    entry = alc.ast_cache_entry_path("k", "castxml")

    assert entry.name == "k.json"
    assert entry.parent.name.startswith("castxml-")
