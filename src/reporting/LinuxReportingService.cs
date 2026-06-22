using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using Google.Protobuf;
using Grpc.Core;
using Microsoft.BusinessCentral.Reporting.Common;
using Microsoft.BusinessCentral.Reporting.Server;
using Microsoft.Dynamics.Nav.Types.Report.Base.Printing;
using Microsoft.Dynamics.Nav.Types.Report.Runtime;
using Microsoft.Reporting.WebForms;
using Newtonsoft.Json;

public sealed class ReportingServiceBridge : ReportingService.ReportingServiceBase
{
    private ReportingServiceSettings settings = new ReportingServiceSettings
    {
        EnableCompactSerialization = true,
        EnableStreamingReportDataset = true,
        EnableAppDomainIsolation = false,
        ProhibitedReportServerPrinters = Array.Empty<string>(),
        ReportAppDomainRecycleCount = 0,
        TraceLevel = 0,
        OpenTelemetryContextColumns = string.Empty,
        OpenTelemetryLogFileFolderOnlyUseDuringDevelopment = string.Empty
    };

    public override Task<Result> ConfigureService(Configuration request, ServerCallContext context)
    {
        try
        {
            settings = JsonConvert.DeserializeObject<ReportingServiceSettings>(request.Settings) ?? settings;
            settings.ProhibitedReportServerPrinters = settings.ProhibitedReportServerPrinters ?? Array.Empty<string>();
            settings.OpenTelemetryContextColumns = settings.OpenTelemetryContextColumns ?? string.Empty;
            settings.OpenTelemetryLogFileFolderOnlyUseDuringDevelopment = settings.OpenTelemetryLogFileFolderOnlyUseDuringDevelopment ?? string.Empty;
            SetSettingsWithoutTelemetry(settings);
            ReportAppDomain.ForceToCurrentAppDomain = true;
            return Task.FromResult(OkResult());
        }
        catch (Exception ex)
        {
            return Task.FromResult(ErrorResult(ex));
        }
    }

    public override Task<ConfigurationResponse> GetServiceConfiguration(Empty request, ServerCallContext context)
    {
        return Task.FromResult(new ConfigurationResponse
        {
            Configuration = new Configuration
            {
                Settings = JsonConvert.SerializeObject(settings)
            }
        });
    }

    public override async Task Render(IAsyncStreamReader<RenderRequest> requestStream, IServerStreamWriter<RenderResponse> responseStream, ServerCallContext context)
    {
        try
        {
            RenderingContext renderingContext = (await ReceiveRenderMessage(requestStream, ReportRequestType.Context, context.CancellationToken)).Context;
            await responseStream.WriteAsync(new RenderResponse { LayoutCached = new LayoutCached { IsCached = false } });
            byte[] layout = await ReceiveLayout(requestStream, renderingContext.LayoutSize, context.CancellationToken);
            byte[][] dataset = await ReceiveDataset(requestStream, renderingContext.ChunkCount, renderingContext.DatasetSize, context.CancellationToken);

            string[] errors;
            byte[] artifact;
            var localization = new ILocalReportHandle.RenderLocalizationSettings
            {
                Culture = CultureInfo.GetCultureInfo(renderingContext.Culture),
                UICulture = CultureInfo.GetCultureInfo(renderingContext.UiCulture),
                Timezone = TimeZoneInfo.FromSerializedString(renderingContext.Timezone)
            };
            using (var report = CreateLocalReportHandle(renderingContext, layout, settings, localization))
            {
                AttachCurrentDomainProxy(report, layout, renderingContext);
                report.AddDataSource(
                    "DataSet_Result",
                    dataset,
                    renderingContext.DatasetIsCompressed,
                    settings.EnableCompactSerialization,
                    renderingContext.DatasetIsValueDeduplicationCompressed);
                SetReportParametersWithoutDiagnostics(report, renderingContext.GetReportParameters());
                artifact = RenderWithoutDiagnostics(report, renderingContext.Format, BuildDeviceInfo(renderingContext, report), out errors);
            }

            if (errors != null && errors.Length != 0)
            {
                await responseStream.WriteAsync(new RenderResponse
                {
                    RenderResult = new RenderResponse.Types.ReportRenderResult
                    {
                        Result = new Result { Error = new Result.Types.Error { Message = string.Join(",", errors) } }
                    }
                });
                return;
            }

            await responseStream.WriteAsync(new RenderResponse
            {
                RenderResult = new RenderResponse.Types.ReportRenderResult
                {
                    Result = OkResult()
                }
            });
            await responseStream.WriteAsync(new RenderResponse
            {
                Stats = new RenderResponse.Types.RenderingStats
                {
                    ArtifactSize = artifact.Length,
                    TotalMemory = 0,
                    TotalProcessorTimeInMs = 0,
                    TotalSurvived = 0
                }
            });

            const int chunkSize = 64 * 1024;
            for (int offset = 0; offset < artifact.Length; offset += chunkSize)
            {
                int count = Math.Min(chunkSize, artifact.Length - offset);
                await responseStream.WriteAsync(new RenderResponse
                {
                    Chunk = new RenderResponse.Types.ArtifactChunk
                    {
                        Data = ByteString.CopyFrom(artifact, offset, count)
                    }
                });
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            await responseStream.WriteAsync(new RenderResponse
            {
                Exception = PackException(ex)
            });
        }
    }

    public override Task PrintReport(IAsyncStreamReader<PrintReportRequest> requestStream, IServerStreamWriter<PrintReportResponse> responseStream, ServerCallContext context)
    {
        return responseStream.WriteAsync(new PrintReportResponse
        {
            Exception = PackException(new NotSupportedException("Server-side printing is not available in this container."))
        });
    }

    private static async Task<RenderRequest> ReceiveRenderMessage(IAsyncStreamReader<RenderRequest> stream, ReportRequestType expected, CancellationToken token)
    {
        if (!await stream.MoveNext(token))
            throw new EndOfStreamException("Unexpected end of render request stream.");
        if (stream.Current.RequestKind != expected)
            throw new InvalidOperationException("Expected " + expected + " but got " + stream.Current.RequestKind + ".");
        return stream.Current;
    }

    private static async Task<byte[]> ReceiveLayout(IAsyncStreamReader<RenderRequest> stream, int expectedSize, CancellationToken token)
    {
        using (var memory = new MemoryStream(expectedSize))
        {
            while (memory.Length < expectedSize)
            {
                RenderRequest request = await ReceiveRenderMessage(stream, ReportRequestType.LayoutChunk, token);
                byte[] bytes = request.LayoutChunk.Data.ToByteArray();
                memory.Write(bytes, 0, bytes.Length);
            }
            if (memory.Length != expectedSize)
                throw new InvalidDataException("Layout size mismatch. Expected " + expectedSize + " bytes, got " + memory.Length + ".");
            return memory.ToArray();
        }
    }

    private static async Task<byte[][]> ReceiveDataset(IAsyncStreamReader<RenderRequest> stream, int chunkCount, int expectedSize, CancellationToken token)
    {
        var chunks = new List<byte[]>(chunkCount);
        int total = 0;
        for (int i = 0; i < chunkCount; i++)
        {
            RenderRequest request = await ReceiveRenderMessage(stream, ReportRequestType.DatasetChunk, token);
            byte[] bytes = request.DatasetChunk.Data.ToByteArray();
            chunks.Add(bytes);
            total += bytes.Length;
        }
        if (total != expectedSize)
            throw new InvalidDataException("Dataset size mismatch. Expected " + expectedSize + " bytes, got " + total + ".");
        return chunks.ToArray();
    }

    private static Result OkResult()
    {
        return new Result { Ok = new Result.Types.Ok() };
    }

    private static LocalReportHandle CreateLocalReportHandle(
        RenderingContext context,
        byte[] layout,
        ReportingServiceSettings settings,
        ILocalReportHandle.RenderLocalizationSettings localization)
    {
        ConstructorInfo[] constructors = typeof(LocalReportHandle)
            .GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        object[] commonArgs =
        {
            context.ReportId,
            layout,
            context.EnableExternalImages,
            context.EnableHyperlinks,
            context.EnableExternalAssemblies,
            settings.EnableAppDomainIsolation,
            false
        };

        ConstructorInfo withLocalization = constructors
            .FirstOrDefault(c => ConstructorMatches(c, commonArgs.Length + 1));
        if (withLocalization != null)
        {
            object[] args = commonArgs.Concat(new object[] { localization }).ToArray();
            return (LocalReportHandle)withLocalization.Invoke(args);
        }

        ConstructorInfo withoutLocalization = constructors
            .FirstOrDefault(c => ConstructorMatches(c, commonArgs.Length));
        if (withoutLocalization != null)
            return (LocalReportHandle)withoutLocalization.Invoke(commonArgs);

        string signatures = string.Join(
            "; ",
            constructors.Select(c => "(" + string.Join(", ", c.GetParameters().Select(p => p.ParameterType.FullName)) + ")"));
        throw new MissingMethodException(
            "No supported LocalReportHandle constructor was found. Available constructors: " + signatures);
    }

    private static bool ConstructorMatches(ConstructorInfo constructor, int parameterCount)
    {
        ParameterInfo[] parameters = constructor.GetParameters();
        if (parameters.Length != parameterCount)
            return false;
        return parameters.Length >= 7
            && parameters[0].ParameterType == typeof(int)
            && parameters[1].ParameterType == typeof(byte[])
            && parameters[2].ParameterType == typeof(bool)
            && parameters[3].ParameterType == typeof(bool)
            && parameters[4].ParameterType == typeof(bool)
            && parameters[5].ParameterType == typeof(bool)
            && parameters[6].ParameterType == typeof(bool);
    }

    private static string BuildDeviceInfo(RenderingContext context, LocalReportHandle report)
    {
        if (context.PaperHeight == 0 && context.PaperWidth == 0)
            return context.EmbedFonts ? string.Empty : "<DeviceInfo><EmbedFonts>None</EmbedFonts></DeviceInfo>";

        double height = context.PaperHeight / 100.0;
        double width = context.PaperWidth / 100.0;
        if (report.isLandScape && height > width)
        {
            double tmp = height;
            height = width;
            width = tmp;
        }

        var writer = new System.Text.StringBuilder();
        writer.Append("<DeviceInfo>");
        if (!context.EmbedFonts)
            writer.Append("<EmbedFonts>None</EmbedFonts>");
        if (!report.PaperFit(new NavPaperSize("custom", context.PaperWidth, context.PaperHeight)))
        {
            writer.AppendFormat(CultureInfo.InvariantCulture, "<PageHeight>{0}in</PageHeight>", height);
            writer.AppendFormat(CultureInfo.InvariantCulture, "<PageWidth>{0}in</PageWidth>", width);
        }
        writer.Append("</DeviceInfo>");
        return writer.ToString();
    }

    private static void AttachCurrentDomainProxy(LocalReportHandle report, byte[] layout, RenderingContext context)
    {
        Type proxyType = typeof(LocalReportHandle).GetNestedType("LocalReportProxy", BindingFlags.NonPublic);
        object proxy = Activator.CreateInstance(
            proxyType,
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
            binder: null,
            args: new object[] { false },
            culture: null);
        proxyType.GetProperty("IsInRemoteAppDomain", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic).SetValue(proxy, false, null);
        proxyType.GetProperty("Name", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic).SetValue(proxy, AppDomain.CurrentDomain.FriendlyName, null);

        LocalReport localReport = (LocalReport)proxyType
            .GetField("localReport", BindingFlags.Instance | BindingFlags.NonPublic)
            .GetValue(proxy);
        localReport.EnableHyperlinks = context.EnableHyperlinks;
        localReport.EnableExternalImages = context.EnableExternalImages;
        if (context.EnableExternalAssemblies)
            throw new NotSupportedException("External report assemblies are not supported for local report rendering.");
        ((Report)localReport).LoadReportDefinition(new MemoryStream(layout));
        PatchReportViewerCasState(localReport);
        typeof(LocalReportHandle)
            .GetField("localReportProxy", BindingFlags.Instance | BindingFlags.NonPublic)
            .SetValue(report, proxy);
    }

    private static void SetReportParametersWithoutDiagnostics(LocalReportHandle report, KeyValuePair<string, string>[] parameters)
    {
        if (parameters == null || parameters.Length == 0)
            return;
        LocalReport localReport = GetLocalReport(report);
        var accepted = new HashSet<string>(localReport.GetParameters().Select(p => p.Name), StringComparer.Ordinal);
        foreach (KeyValuePair<string, string> parameter in parameters)
        {
            if (accepted.Contains(parameter.Key))
                ((Report)localReport).SetParameters(new ReportParameter(parameter.Key, parameter.Value));
        }
    }

    private static LocalReport GetLocalReport(LocalReportHandle report)
    {
        object proxy = typeof(LocalReportHandle)
            .GetField("localReportProxy", BindingFlags.Instance | BindingFlags.NonPublic)
            .GetValue(report);
        return (LocalReport)proxy.GetType()
            .GetField("localReport", BindingFlags.Instance | BindingFlags.NonPublic)
            .GetValue(proxy);
    }

    private static byte[] RenderWithoutDiagnostics(LocalReportHandle report, string format, string deviceInfo, out string[] errors)
    {
        LocalReport localReport = GetLocalReport(report);
        string mimeType = null;
        string encoding = null;
        string extension = null;
        string[] streams = null;
        Warning[] warnings = null;
        byte[] output = ((Report)localReport).Render(format, deviceInfo, out mimeType, out encoding, out extension, out streams, out warnings);
        errors = warnings == null
            ? Array.Empty<string>()
            : warnings.Where(w => w.Severity == Severity.Error).Select(w => w.Message).ToArray();
        return output;
    }

    private static void PatchReportViewerCasState(LocalReport localReport)
    {
        object processingHost = typeof(LocalReport)
            .GetField("m_processingHost", BindingFlags.Instance | BindingFlags.NonPublic)
            .GetValue(localReport);
        Type localServiceType = processingHost.GetType();
        while (localServiceType != null && localServiceType.FullName != "Microsoft.Reporting.LocalService")
            localServiceType = localServiceType.BaseType;
        if (localServiceType == null)
            return;
        object handler = localServiceType
            .GetField("m_reportRuntimeSetupHandler", BindingFlags.Instance | BindingFlags.NonPublic)
            .GetValue(processingHost);
        Type handlerType = handler.GetType();
        Type triStateType = handlerType.GetNestedType("TriState", BindingFlags.NonPublic);
        object trueState = Enum.Parse(triStateType, "True");
        object falseState = Enum.Parse(triStateType, "False");
        handlerType.GetField("m_isAppDomainCasPolicyEnabled", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(handler, trueState);
        handlerType.GetField("m_executeInSandbox", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(handler, falseState);
    }

    private static Result ErrorResult(Exception ex)
    {
        return new Result { Exception = PackException(ex) };
    }

    private static ServerException PackException(Exception ex)
    {
        return new ServerException { Message = ex.Message, Type = ex.GetType().FullName };
    }

    private static void SetSettingsWithoutTelemetry(ReportingServiceSettings value)
    {
        typeof(ReportingServiceSettings)
            .GetField("instance", BindingFlags.Static | BindingFlags.NonPublic)
            .SetValue(null, value);
    }
}

public static class Program
{
    public static void Main(string[] args)
    {
        AppDomain.CurrentDomain.AssemblyResolve += ResolveSideServiceAssembly;
        int port = args.Length > 0 ? int.Parse(args[0]) : 17778;
        var server = new Server
        {
            Services = { ReportingService.BindService(new ReportingServiceBridge()) },
            Ports = { new ServerPort("localhost", port, ServerCredentials.Insecure) }
        };
        server.Start();
        Console.WriteLine("Reporting service bridge listening on " + port);
        Task.Delay(Timeout.Infinite).Wait();
    }

    private static Assembly ResolveSideServiceAssembly(object sender, ResolveEventArgs args)
    {
        return null;
    }
}
