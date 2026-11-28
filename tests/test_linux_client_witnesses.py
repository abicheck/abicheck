import platform
import shutil
import sys

import pytest

from abiforge.run.linux_client_witnesses import plt_binding, virtual_deletion

pytestmark = pytest.mark.skipif(
    sys.platform != "linux"
    or platform.machine() != "x86_64"
    or not all(shutil.which(tool) for tool in ("gcc", "g++", "readelf")),
    reason="requires GNU tools and the Linux x86-64 ELF ABI",
)


def test_plt_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("LD_BIND_NOW", "1")
    entered = "entered main\n"
    assert plt_binding(tmp_path) == {
        ("baseline", "lazy", "keep"): (0, entered),
        ("baseline", "lazy", "removed"): (0, entered),
        ("baseline", "now", "keep"): (0, entered),
        ("baseline", "now", "removed"): (0, entered),
        ("removed", "lazy", "keep"): (0, entered),
        ("removed", "lazy", "removed"): (127, entered),
        ("removed", "now", "keep"): (127, ""),
        ("removed", "now", "removed"): (127, ""),
    }


def test_virtual_deletion(tmp_path):
    assert virtual_deletion(tmp_path) == {
        "baseline": (0, "1 1\n"),
        "removed": (42, "wrong deleting slot\n"),
    }
