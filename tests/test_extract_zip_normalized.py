import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = REPO_ROOT / "scripts" / "extract-zip-normalized.py"


class ExtractZipNormalizedTests(unittest.TestCase):
    def test_extracts_windows_backslash_paths_as_linux_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "artifact.zip"
            dest = tmp_path / "out"

            with zipfile.ZipFile(archive, "w") as zf:
                for name, body in [
                    ("manifest.json", '{"platform": "1.0"}'),
                    ("folder\\file.txt", "ok"),
                ]:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 0
                    zf.writestr(info, body)

            result = subprocess.run(
                [sys.executable, str(EXTRACTOR), str(archive), str(dest)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((dest / "manifest.json").read_text(), '{"platform": "1.0"}')
            self.assertEqual((dest / "folder" / "file.txt").read_text(), "ok")

    def test_matches_globs_case_insensitively_after_normalizing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "platform.zip"
            dest = tmp_path / "out"

            with zipfile.ZipFile(archive, "w") as zf:
                info = zipfile.ZipInfo("Applications\\BaseApp\\Source\\Microsoft_Application.app")
                info.create_system = 0
                zf.writestr(info, "app")

            result = subprocess.run(
                [sys.executable, str(EXTRACTOR), str(archive), str(dest), "applications/*"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (dest / "Applications" / "BaseApp" / "Source" / "Microsoft_Application.app").read_text(),
                "app",
            )


if __name__ == "__main__":
    unittest.main()
