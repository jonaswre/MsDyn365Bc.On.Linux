import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


SYSTEM_APPLICATION_ID = "63ca2fa4-4f03-4f2b-a480-172fef340d3f"
BUSINESS_FOUNDATION_ID = "f3552374-a1f2-4356-848e-196002525837"
BASE_APPLICATION_ID = "437dbf0e-84ff-417a-965d-ed2bb9650972"
APPLICATION_ID = "c1335042-3002-4257-bf8a-75c898ccb1b8"


def write_app(path: Path, app_id: str, name: str, application: str = "", dependencies: list[str] | None = None) -> None:
    application_attr = f' Application="{application}"' if application else ""
    deps = "\n".join(
        f'    <Dependency Id="{dep}" Name="Dep" Publisher="Microsoft" MinVersion="1.0.0.0" />'
        for dep in dependencies or []
    )
    manifest = f"""<Package xmlns="http://schemas.microsoft.com/navx/2015/manifest">
  <App Id="{app_id}" Name="{name}" Publisher="Microsoft" Version="1.0.0.0"{application_attr} />
  <Dependencies>
{deps}
  </Dependencies>
</Package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("NavxManifest.xml", manifest)


class ResolveKeepAppIdsTests(unittest.TestCase):
    def test_app_file_application_shorthand_keeps_application_stack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            consumer = root / "consumer.app"

            write_app(artifact_dir / "system-application.app", SYSTEM_APPLICATION_ID, "System Application")
            write_app(artifact_dir / "business-foundation.app", BUSINESS_FOUNDATION_ID, "Business Foundation")
            write_app(artifact_dir / "base-application.app", BASE_APPLICATION_ID, "Base Application")
            write_app(artifact_dir / "application.app", APPLICATION_ID, "Application")
            write_app(consumer, "11111111-1111-1111-1111-111111111111", "Consumer", application="27.0.0.0")

            result = subprocess.run(
                [
                    "python3",
                    "scripts/resolve-keep-app-ids.py",
                    "--artifact-dir",
                    str(artifact_dir),
                    "--app-file",
                    str(consumer),
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )

            keep_ids = {item.strip() for item in result.stdout.split(",") if item.strip()}
            self.assertIn(SYSTEM_APPLICATION_ID, keep_ids)
            self.assertIn(BUSINESS_FOUNDATION_ID, keep_ids)
            self.assertIn(BASE_APPLICATION_ID, keep_ids)
            self.assertIn(APPLICATION_ID, keep_ids)


if __name__ == "__main__":
    unittest.main()
