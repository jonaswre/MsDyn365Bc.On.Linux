# Build stage: compile StartupHook + stubs + tools
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS builder

ARG BC_TYPE=sandbox
ARG BC_VERSION=28.1
ARG BC_COUNTRY=w1
ARG AL_TOOL_VERSION=17.0.34.45391

SHELL ["/bin/bash", "-c"]

# gcc needed for compiling Win32 P/Invoke stubs (kernel32_stubs.c → libwin32_stubs.so)
RUN apt-get update && apt-get install -y --no-install-recommends curl gcc libc6-dev python3 unzip && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY src/ src/
COPY tools/ tools/
COPY scripts/ scripts/
COPY extensions/TestRunnerExtension/ extensions/TestRunnerExtension/
RUN cd src/StartupHook && dotnet publish -c Release -o /build/output/hook

RUN cp /build/src/stubs/DrawingStub/bin/Release/net8.0/System.Drawing.Common.dll /build/output/DrawingStub.dll

# Compile the AL test runner extension from source so service-enabled API
# changes are included in the image instead of relying on a checked-in .app.
RUN set -euo pipefail; \
    PKG="microsoft.dynamics.businesscentral.development.tools.linux"; \
    EXTRACT_DIR="/build/al-tool/${AL_TOOL_VERSION}"; \
    mkdir -p "${EXTRACT_DIR}" /build/al-bin /build/al-artifacts /build/al-symbols /build/output/testrunner; \
    curl -fsSL "https://api.nuget.org/v3-flatcontainer/${PKG}/${AL_TOOL_VERSION}/${PKG}.${AL_TOOL_VERSION}.nupkg" -o "${EXTRACT_DIR}/pkg.zip"; \
    unzip -q -o "${EXTRACT_DIR}/pkg.zip" -d "${EXTRACT_DIR}"; \
    rm "${EXTRACT_DIR}/pkg.zip"; \
    if [ -d "${EXTRACT_DIR}/tools/net8.0/any" ]; then AL_TOOL_DIR="${EXTRACT_DIR}/tools/net8.0/any"; else AL_TOOL_DIR="${EXTRACT_DIR}/lib/net8.0"; fi; \
    chmod +x "${AL_TOOL_DIR}/alc" 2>/dev/null || true; \
    printf '#!/usr/bin/env bash\nset -euo pipefail\nif [ "${1:-}" = "compile" ]; then shift; fi\nexec "%s/alc" "$@"\n' "${AL_TOOL_DIR}" > /build/al-bin/AL; \
    chmod +x /build/al-bin/AL; \
    /build/scripts/download-artifacts.sh "${BC_TYPE}" "${BC_VERSION}" "${BC_COUNTRY}" /build/al-artifacts; \
    python3 /build/scripts/stage-symbols.py \
        --artifact-dir /build/al-artifacts \
        --out-dir /build/al-symbols \
        --app-json /build/extensions/TestRunnerExtension/app.json; \
    mkdir -p /build/extensions/TestRunnerExtension/.alpackages; \
    cp /build/al-symbols/*.app /build/extensions/TestRunnerExtension/.alpackages/ 2>/dev/null || true; \
    PATH="/build/al-bin:${PATH}" AL compile \
        "/project:/build/extensions/TestRunnerExtension" \
        "/packagecachepath:/build/extensions/TestRunnerExtension/.alpackages" \
        "/out:/build/output/testrunner/TestRunner.app"

# Publish tools
RUN cd src/tools/MergeNetstandard && dotnet publish -c Release -o /build/output/tools 2>&1 | tail -3
RUN cd src/tools/PatchNclTestPage && dotnet publish -c Release -o /build/output/tools/patcher 2>&1 | tail -3
# TestRunner — bundled in the image so run-tests.sh can invoke it via
# `docker compose exec` instead of `dotnet run`, removing the host-side
# .NET 8 SDK requirement for running tests against a Linux BC container.
RUN cd tools/TestRunner && dotnet publish -c Release -o /build/output/tools/TestRunner 2>&1 | tail -3

# Copy .NET 8 reference assemblies from SDK (needed for Cecil type resolution)
RUN mkdir -p /build/output/refasm && \
    REF_DIR=$(find /usr/share/dotnet/packs/Microsoft.NETCore.App.Ref -maxdepth 3 -type d -name "net8.0" | head -1) && \
    cp "$REF_DIR"/*.dll /build/output/refasm/ && \
    echo "Copied $(ls /build/output/refasm/*.dll | wc -l) reference assemblies"

# Runtime stage: minimal image
FROM mcr.microsoft.com/dotnet/aspnet:8.0

# Install tools for artifact download and SQL setup + en_US locale
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip python3 gnupg locales && \
    sed -i '/en_US.UTF-8/s/^# //' /etc/locale.gen && locale-gen && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql.list && \
    ACCEPT_EULA=Y apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends mssql-tools18 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /bc/hook /bc/patched /bc/refasm /bc/addins-overlay /bc/testrunner /bc/service /bc/artifacts /bc/scripts /bc/tools

# Copy built artifacts from builder
COPY --from=builder /build/output/hook/ /bc/hook/
COPY --from=builder /build/output/refasm/ /bc/refasm/
COPY --from=builder /build/output/tools/ /bc/tools/
COPY --from=builder /build/output/DrawingStub.dll /bc/addins-overlay/System.Drawing.Common.dll

# Copy scripts
COPY scripts/ /bc/scripts/
RUN chmod +x /bc/scripts/*.sh

# Copy test runner extension (REST API for test execution)
COPY --from=builder /build/output/testrunner/TestRunner.app /bc/testrunner/TestRunner.app

# Copy SideService stubs (Linux replacements for Windows PE executables)
COPY stubs/reporting-service-stub /bc/stubs/reporting-service-stub
COPY stubs/reporting-service-stub.dll /bc/stubs/reporting-service-stub.dll
COPY stubs/reporting-service-stub.runtimeconfig.json /bc/stubs/reporting-service-stub.runtimeconfig.json

ENV DOTNET_STARTUP_HOOKS=/bc/hook/StartupHook.dll \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8
EXPOSE 7045-7054 7083 7085-7089

ENTRYPOINT ["/bc/scripts/entrypoint.sh"]
