# Build stage: compile StartupHook + stubs + tools
FROM --platform=linux/amd64 mcr.microsoft.com/dotnet/sdk:8.0 AS builder

# gcc needed for compiling Win32 P/Invoke stubs (kernel32_stubs.c → libwin32_stubs.so)
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY src/ src/
RUN cd src/StartupHook && dotnet publish -c Release -o /build/output/hook
# Web client startup hook (separate from the NST hook — see docs/WEBCLIENT-POC.md)
RUN cd src/WebClientHook && dotnet publish -c Release -o /build/output/webclient-hook

RUN cp /build/src/stubs/DrawingStub/bin/Release/net8.0/System.Drawing.Common.dll /build/output/DrawingStub.dll

# Publish tools
RUN cd src/tools/MergeNetstandard && dotnet publish -c Release -o /build/output/tools 2>&1 | tail -3
RUN cd src/tools/PatchNclTestPage && dotnet publish -c Release -o /build/output/tools/patcher 2>&1 | tail -3
RUN cd src/tools/PatchReportViewerLinux && dotnet publish -c Release -o /build/output/tools/reportviewer 2>&1 | tail -3

# Copy .NET 8 reference assemblies from SDK (needed for Cecil type resolution)
RUN mkdir -p /build/output/refasm && \
    REF_DIR=$(find /usr/share/dotnet/packs/Microsoft.NETCore.App.Ref -maxdepth 3 -type d -name "net8.0" | head -1) && \
    cp "$REF_DIR"/*.dll /build/output/refasm/ && \
    echo "Copied $(ls /build/output/refasm/*.dll | wc -l) reference assemblies"

# Runtime stage: minimal image
FROM --platform=linux/amd64 mcr.microsoft.com/dotnet/aspnet:8.0

# Install tools for artifact download and SQL setup + en_US locale
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip python3 gnupg locales \
        wine wine64 wine64-preloader mono-devel libgdiplus fontconfig \
        fonts-dejavu-core fonts-liberation fonts-crosextra-carlito && \
    sed -i '/en_US.UTF-8/s/^# //' /etc/locale.gen && locale-gen && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql.list && \
    ACCEPT_EULA=Y apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends mssql-tools18 && \
    curl -fsSL -o /tmp/wine-mono-7.4.0-x86.msi https://dl.winehq.org/wine/wine-mono/7.4.0/wine-mono-7.4.0-x86.msi && \
    WINEDEBUG=-all wine msiexec /i /tmp/wine-mono-7.4.0-x86.msi && \
    mkdir -p /root/.wine/drive_c/windows/Fonts && \
    cp /usr/share/fonts/truetype/dejavu/*.ttf /root/.wine/drive_c/windows/Fonts/ && \
    cp /usr/share/fonts/truetype/liberation/*.ttf /root/.wine/drive_c/windows/Fonts/ && \
    mkdir -p /bc/native && \
    curl -fsSL -o /tmp/grpc.core.2.46.6.nupkg https://api.nuget.org/v3-flatcontainer/grpc.core/2.46.6/grpc.core.2.46.6.nupkg && \
    unzip -p /tmp/grpc.core.2.46.6.nupkg runtimes/linux-x64/native/libgrpc_csharp_ext.x64.so > /bc/native/libgrpc_csharp_ext.x64.so && \
    chmod 755 /bc/native/libgrpc_csharp_ext.x64.so && \
    fc-cache -f && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /bc/hook /bc/patched /bc/refasm /bc/addins-overlay /bc/service /bc/artifacts /bc/scripts /bc/tools /bc/reporting /bc/native

# Copy built artifacts from builder
COPY --from=builder /build/output/hook/ /bc/hook/
COPY --from=builder /build/output/webclient-hook/ /bc/webclient-hook/
COPY --from=builder /build/output/refasm/ /bc/refasm/
COPY --from=builder /build/output/tools/ /bc/tools/
COPY --from=builder /build/output/DrawingStub.dll /bc/addins-overlay/System.Drawing.Common.dll

# Copy scripts
COPY scripts/ /bc/scripts/
RUN chmod +x /bc/scripts/*.sh /bc/scripts/*.py

# Copy reporting bridge source. It is compiled after BC artifacts are downloaded
# because it references version-matched Microsoft reporting assemblies.
COPY src/reporting/LinuxReportingService.cs /bc/reporting/ReportingServiceBridge.cs

# Copy SideService stubs (Linux replacements for Windows PE executables)
COPY stubs/reporting-service-stub /bc/stubs/reporting-service-stub
COPY stubs/reporting-service-stub.dll /bc/stubs/reporting-service-stub.dll
COPY stubs/reporting-service-stub.runtimeconfig.json /bc/stubs/reporting-service-stub.runtimeconfig.json

ENV DOTNET_STARTUP_HOOKS=/bc/hook/StartupHook.dll \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8
EXPOSE 7045 7046 7047 7048 7049 7052 7085 7086

ENTRYPOINT ["/bc/scripts/entrypoint.sh"]
