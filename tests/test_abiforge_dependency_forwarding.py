import sys

import pytest

from abiforge.run.dependency_forwarding import run


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses the ELF dynamic loader")
def test_dependency_forwarding_order(tmp_path):
    assert run(tmp_path) == {
        "baseline": 42,
        "forward_copy": 42,
        "drop_dependency": 2,
        "reverse_order": 20,
    }
