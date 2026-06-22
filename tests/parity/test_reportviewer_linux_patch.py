import unittest
import re
from pathlib import Path


class ReportViewerLinuxPatchTests(unittest.TestCase):
    def test_reportviewer_patcher_targets_impersonation_wrappers(self):
        source = Path("src/tools/PatchReportViewerLinux/Program.cs").read_text(encoding="utf-8")

        self.assertIn("Microsoft.ReportingServices.Diagnostics.RevertImpersonationContext", source)
        self.assertIn("Run", source)
        self.assertIn("RunFromRestrictedCasContext", source)
        self.assertIn("ContextBody::Invoke", source)
        self.assertIn("Microsoft.ReportingServices.RdlExpressions.ExprHostCompiler", source)
        self.assertIn("InternalCompile", source)
        self.assertIn("System.IO.File::Delete", source)
        self.assertIn("Microsoft.ReportingServices.Rendering.RichText.FontCache", source)
        self.assertIn("CreateGdiPlusFont", source)
        self.assertIn("DejaVu Sans", source)

    def test_image_build_publishes_reportviewer_patcher(self):
        dockerfile = Path("src/Dockerfile").read_text(encoding="utf-8")

        self.assertIn("src/tools/PatchReportViewerLinux", dockerfile)
        self.assertIn("/build/output/tools/reportviewer", dockerfile)
        self.assertIn("grpc.core/2.46.6", dockerfile)
        self.assertIn("/bc/native/libgrpc_csharp_ext.x64.so", dockerfile)

    def test_entrypoint_applies_reportviewer_patcher_before_startup(self):
        entrypoint = Path("scripts/entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("/bc/tools/reportviewer/PatchReportViewerLinux.dll", entrypoint)
        self.assertIn("Microsoft.ReportViewer.Common.dll", entrypoint)
        self.assertIn("Patched ReportViewer.Common.dll (report rendering compatibility)", entrypoint)
        self.assertIn("REPORTVIEWER_PATCH_LOG", entrypoint)
        self.assertNotIn("Linux/Wine report rendering", entrypoint)

    def test_entrypoint_compiles_and_starts_linux_reporting_sidecar(self):
        entrypoint = Path("scripts/entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("BC_ENABLE_REPORTING_BRIDGE", entrypoint)
        self.assertIn("BC_ENABLE_WINE_REPORTING:-true", entrypoint)
        self.assertIn("ReportingServiceBridge.exe", entrypoint)
        self.assertIn("mcs -langversion:latest", entrypoint)
        self.assertIn("wine \"$REPORTING_EXE\" \"$BC_REPORTING_GRPC_PORT\"", entrypoint)
        self.assertIn("/tmp/reporting-service-bridge.log", entrypoint)
        self.assertIn("/bc/reporting/ReportingServiceBridge.cs", entrypoint)
        self.assertIn("Compiling reporting service bridge", entrypoint)
        self.assertIn("Started reporting service bridge", entrypoint)
        self.assertNotIn("LinuxReportingService.exe", entrypoint)
        self.assertNotIn("/tmp/linux-reporting-service.log", entrypoint)
        self.assertNotIn("Compiling Linux reporting sidecar", entrypoint)
        self.assertNotIn("Started Linux reporting sidecar", entrypoint)

    def test_entrypoint_exposes_grpc_native_extension_for_reporting_client(self):
        entrypoint = Path("scripts/entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("SideServices/libgrpc_csharp_ext.x64.so", entrypoint)
        self.assertIn("/bc/native/libgrpc_csharp_ext.x64.so", entrypoint)
        self.assertIn("$SERVICE_DIR/libgrpc_csharp_ext.x64.so", entrypoint)
        self.assertIn("Copied gRPC native extension for reporting client", entrypoint)

    def test_linux_reporting_sidecar_has_required_rendering_paths(self):
        source = Path("src/reporting/LinuxReportingService.cs").read_text(encoding="utf-8")

        self.assertIn("public override async Task Render", source)
        self.assertIn("RenderingContext", source)
        self.assertIn("LayoutCached", source)
        self.assertIn("LocalReportHandle", source)
        self.assertIn("DataSet_Result", source)
        self.assertIn("RenderWithoutDiagnostics", source)
        self.assertIn("public sealed class ReportingServiceBridge", source)
        self.assertIn("Server-side printing is not available in this container", source)
        self.assertNotIn("not supported by the Linux reporting sidecar", source)
        self.assertNotIn("Linux reporting sidecar listening", source)
        self.assertIn("Reporting service bridge listening", source)

    def test_linux_reporting_sidecar_supports_older_report_handle_constructors(self):
        source = Path("src/reporting/LinuxReportingService.cs").read_text(encoding="utf-8")

        self.assertIn("CreateLocalReportHandle", source)
        self.assertIn("ConstructorMatches", source)
        self.assertIn("commonArgs.Length + 1", source)
        self.assertIn("commonArgs.Length", source)
        self.assertNotIn("new LocalReportHandle(", source)

    def test_runtime_reporting_logs_do_not_expose_platform_bridge(self):
        sources = [
            Path("scripts/entrypoint.sh").read_text(encoding="utf-8"),
            Path("src/reporting/LinuxReportingService.cs").read_text(encoding="utf-8"),
            Path("src/tools/PatchReportViewerLinux/Program.cs").read_text(encoding="utf-8"),
            Path("src/StartupHook/StartupHook.cs").read_text(encoding="utf-8"),
        ]
        string_literals = "\n".join(
            literal
            for source in sources
            for literal in re.findall(r'"(?:\\.|[^"\\])*"', source)
        )

        for forbidden in (
            "Linux/Wine report rendering",
            "Linux reporting sidecar",
            "Wine reporting sidecar",
            "not supported by the Linux reporting sidecar",
            "LinuxReportingService.exe",
            "/tmp/linux-reporting-service.log",
        ):
            self.assertNotIn(forbidden, string_literals)

    def test_startup_hook_patch_diagnostics_are_opt_in(self):
        source = Path("src/StartupHook/StartupHook.cs").read_text(encoding="utf-8")

        self.assertIn("BC_VERBOSE_COMPATIBILITY_PATCHES", source)
        self.assertIn("private static void Log(string message)", source)
        self.assertNotIn('Console.WriteLine("[StartupHook] Patch #19', source)
        self.assertNotIn('Console.WriteLine($"[StartupHook] Patch #19', source)
        self.assertNotIn('Console.Error.WriteLine("[StartupHook] Patch #19', source)
        self.assertNotIn('Console.Error.WriteLine($"[StartupHook] Patch #19', source)


if __name__ == "__main__":
    unittest.main()
