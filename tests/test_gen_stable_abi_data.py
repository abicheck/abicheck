from __future__ import annotations

import importlib.util
import py_compile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_stable_abi_data.py"
_SPEC = importlib.util.spec_from_file_location("gen_stable_abi_data", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
gen_stable_abi_data = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen_stable_abi_data)


def test_extract_rejects_non_c_symbol_names() -> None:
    toml_bytes = br'''
[function."PySafe"]
added = "3.2"

[function."PyBad\"\n, **(__import__('os').system('echo pwned') and {}) #"]
added = "3.14"
'''

    with pytest.raises(ValueError, match="invalid Stable-ABI symbol name"):
        gen_stable_abi_data.extract(toml_bytes)


def test_render_uses_python_literals_for_data_and_docstring(tmp_path: Path) -> None:
    payload = "3.15\"\"\"\n__import__('pathlib').Path('pwned').write_text('x')\n\"\"\""
    rendered = gen_stable_abi_data.render({"PySafe": (3, 2)}, payload)
    out = tmp_path / "stable_abi_data.py"
    out.write_text(rendered, encoding="utf-8")

    py_compile.compile(str(out), doraise=True)
    assert "'PySafe': (3, 2)" in rendered
    assert "__import__" in rendered
