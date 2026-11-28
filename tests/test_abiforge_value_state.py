import sys

import pytest

from abiforge.run.value_state import run


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses the ELF dynamic loader")
def test_value_state_contract(tmp_path):
    assert run(tmp_path) == {
        "baseline": (0, 7, 7, 7, 0),
        "equivalent": (0, 7, 7, 7, 0),
        "reject_success": (7, 7, 7, 7, 0),
        "swallow_error": (0, 0, 7, 7, 0),
        "discard_copy": (0, 7, 0, 7, 0),
        "discard_assignment": (0, 7, 7, 0, 0),
        "ignore_reset": (0, 7, 7, 7, 7),
    }
