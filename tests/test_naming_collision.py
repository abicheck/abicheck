"""Tests for naming collision clarity (6a-6c)."""
import subprocess
import sys


class TestMainModuleImport:
    def test_main_module_importable(self):
        """__main__.py can be imported without side effects."""
        import abicheck.__main__ as mod
        from abicheck.cli import main
        assert mod.main is main


class TestMainModule:
    def test_python_m_abicheck_version(self):
        """python -m abicheck --version should work."""
        result = subprocess.run(
            [sys.executable, "-m", "abicheck", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "abicheck/abicheck" in result.stdout

    def test_version_output_format(self):
        """Version output should include project qualifier."""
        result = subprocess.run(
            [sys.executable, "-m", "abicheck", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        # Format: "abicheck X.Y.Z (abicheck/abicheck)"
        assert "(abicheck/abicheck)" in result.stdout
