"""Tests for the delta-based module architecture gate."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import module_architecture as gate  # noqa: E402


class ModuleArchitectureUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = gate.load_config(
            ROOT / "architecture" / "module-boundaries.json"
        )

    def test_base_ref_resolution_prefers_cli_then_environment(self) -> None:
        self.assertEqual(
            gate.resolve_base_ref("base-sha", {gate.BASE_REF_ENV: "env-sha"}),
            "base-sha",
        )
        self.assertEqual(
            gate.resolve_base_ref(None, {gate.BASE_REF_ENV: "env-sha"}),
            "env-sha",
        )
        self.assertEqual(gate.resolve_base_ref(None, {}), "origin/main")

    def test_parse_name_status_handles_rename(self) -> None:
        changes = gate.parse_name_status(
            "M\tabicheck/a.py\nA\tabicheck/b.py\nR100\tabicheck/c.py\tabicheck/d.py\n"
        )
        self.assertEqual(
            changes,
            [
                gate.Change("M", "abicheck/a.py"),
                gate.Change("A", "abicheck/b.py"),
                gate.Change("R100", "abicheck/d.py", "abicheck/c.py"),
            ],
        )

    def test_new_top_level_overflow_family_is_rejected(self) -> None:
        findings = gate.check_size(
            path="abicheck/cli_more_helpers.py",
            current="x = 1\n",
            base=None,
            is_new=True,
            config=self.config,
        )
        self.assertIn(
            "top-level-overflow-module", {finding.check for finding in findings}
        )

    def test_new_top_level_overflow_package_is_rejected(self) -> None:
        findings = gate.check_size(
            path="abicheck/cli_more/__init__.py",
            current="",
            base=None,
            is_new=True,
            config=self.config,
        )
        self.assertIn(
            "top-level-overflow-module", {finding.check for finding in findings}
        )

    def test_new_production_module_over_800_lines_is_rejected(self) -> None:
        findings = gate.check_size(
            path="abicheck/evidence/oversized.py",
            current="x = 1\n" * 801,
            base=None,
            is_new=True,
            config=self.config,
        )
        self.assertIn("new-file-size", {finding.check for finding in findings})

    def test_target_layer_name_must_be_a_package(self) -> None:
        findings = gate.check_size(
            path="abicheck/domain.py",
            current="x = 1\n",
            base=None,
            is_new=True,
            config=self.config,
        )
        self.assertIn(
            "target-layer-must-be-package", {finding.check for finding in findings}
        )

    def test_legacy_large_module_may_not_grow(self) -> None:
        findings = gate.check_size(
            path="abicheck/legacy.py",
            current="x = 1\n" * 1001,
            base="x = 1\n" * 1000,
            is_new=False,
            config=self.config,
        )
        self.assertIn("legacy-file-growth", {finding.check for finding in findings})

    def test_legacy_large_module_may_shrink(self) -> None:
        findings = gate.check_size(
            path="abicheck/legacy.py",
            current="x = 1\n" * 900,
            base="x = 1\n" * 1000,
            is_new=False,
            config=self.config,
        )
        self.assertNotIn("legacy-file-growth", {finding.check for finding in findings})

    def test_target_layer_rejects_reverse_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "bad.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "from abicheck.interfaces import cli\n", encoding="utf-8"
            )
            findings = gate.check_imports(
                root, ["abicheck/domain/bad.py"], self.config
            )
        self.assertEqual(
            {finding.check for finding in findings},
            {"architecture-import-direction"},
        )

    def test_target_layer_allows_declared_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "evidence" / "reader.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "from abicheck.storage import snapshot\n", encoding="utf-8"
            )
            findings = gate.check_imports(
                root, ["abicheck/evidence/reader.py"], self.config
            )
        self.assertEqual(findings, [])

    def test_legacy_reverse_dependency_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "bad.py"
            path.parent.mkdir(parents=True)
            path.write_text("from abicheck import cli\n", encoding="utf-8")
            findings = gate.check_imports(
                root, ["abicheck/domain/bad.py"], self.config
            )
        self.assertEqual(len(findings), 1)
        self.assertIn("domain imports interfaces", findings[0].message)

    def test_non_dumper_evidence_import_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "bad.py"
            path.parent.mkdir(parents=True)
            path.write_text("from abicheck import elf_metadata\n", encoding="utf-8")
            findings = gate.check_imports(
                root, ["abicheck/domain/bad.py"], self.config
            )
        self.assertEqual(len(findings), 1)
        self.assertIn("domain imports evidence", findings[0].message)

    def test_unclassified_first_party_import_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "bad.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "from abicheck import unclassified_legacy\n", encoding="utf-8"
            )
            findings = gate.check_imports(
                root, ["abicheck/domain/bad.py"], self.config
            )
        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].check, "architecture-unclassified-first-party-import"
        )

    def test_legacy_same_layer_dependency_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "catalog.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "from abicheck import change_registry\n", encoding="utf-8"
            )
            findings = gate.check_imports(
                root, ["abicheck/domain/catalog.py"], self.config
            )
        self.assertEqual(findings, [])

    def test_relative_reverse_dependency_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "nested" / "bad.py"
            path.parent.mkdir(parents=True)
            path.write_text("from ...interfaces import cli\n", encoding="utf-8")
            findings = gate.check_imports(
                root, ["abicheck/domain/nested/bad.py"], self.config
            )
        self.assertEqual(len(findings), 1)
        self.assertIn("domain imports interfaces", findings[0].message)

    def test_package_init_relative_import_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "__init__.py"
            path.parent.mkdir(parents=True)
            path.write_text("from .. import interfaces\n", encoding="utf-8")
            findings = gate.check_imports(
                root, ["abicheck/domain/__init__.py"], self.config
            )
        self.assertEqual(len(findings), 1)
        self.assertIn("domain imports interfaces", findings[0].message)

    def test_from_parent_import_layer_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "abicheck" / "domain" / "bad.py"
            path.parent.mkdir(parents=True)
            path.write_text("from abicheck import interfaces\n", encoding="utf-8")
            findings = gate.check_imports(
                root, ["abicheck/domain/bad.py"], self.config
            )
        self.assertEqual(len(findings), 1)
        self.assertIn("domain imports interfaces", findings[0].message)


@unittest.skipUnless(shutil.which("git"), "git is required")
class ModuleArchitectureGitIntegrationTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        return proc.stdout.strip()

    def test_run_checks_compares_large_file_to_base_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture").mkdir()
            shutil.copy2(
                ROOT / "architecture" / "module-boundaries.json",
                root / "architecture" / "module-boundaries.json",
            )
            module = root / "abicheck" / "legacy.py"
            module.parent.mkdir()
            module.write_text("x = 1\n" * 900, encoding="utf-8")

            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "test@example.com")
            self._git(root, "config", "user.name", "Test")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "base")
            base = self._git(root, "rev-parse", "HEAD")

            module.write_text("x = 1\n" * 901, encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "grow")

            config = gate.load_config(
                root / "architecture" / "module-boundaries.json"
            )
            findings = gate.run_checks(root, config, base)

        self.assertIn("legacy-file-growth", {item.check for item in findings})

    def test_run_checks_reads_old_content_from_merge_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture").mkdir()
            shutil.copy2(
                ROOT / "architecture" / "module-boundaries.json",
                root / "architecture" / "module-boundaries.json",
            )
            module = root / "abicheck" / "legacy.py"
            module.parent.mkdir()
            module.write_text("x = 1\n" * 900, encoding="utf-8")

            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "test@example.com")
            self._git(root, "config", "user.name", "Test")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "common")
            common = self._git(root, "rev-parse", "HEAD")

            self._git(root, "branch", "feature", common)
            module.write_text("x = 1\n" * 950, encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "advance base")
            advanced_base = self._git(root, "rev-parse", "HEAD")

            self._git(root, "checkout", "-q", "feature")
            module.write_text("x = 1\n" * 901, encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "feature growth")

            config = gate.load_config(
                root / "architecture" / "module-boundaries.json"
            )
            findings = gate.run_checks(root, config, advanced_base)

        self.assertIn("legacy-file-growth", {item.check for item in findings})


if __name__ == "__main__":
    unittest.main()
