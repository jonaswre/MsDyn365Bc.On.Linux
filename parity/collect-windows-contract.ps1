param(
    [Parameter(Mandatory = $true)][string]$BcVersion,
    [Parameter(Mandatory = $true)][string]$SmokeAppPath,
    [Parameter(Mandatory = $true)][string]$OutJson,
    [string]$ContainerName = "bc-parity",
    [string]$Username = "admin",
    [string]$Password = "admin"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$contractsDir = Split-Path -Parent $OutJson
$expectedTests = 7
if (-not $contractsDir) {
    $contractsDir = "."
}
New-Item -ItemType Directory -Force -Path $contractsDir | Out-Null

function Fail-Capability($Code, $Message) {
    Write-Error "$Code`: $Message"
    exit 1
}

function Invoke-NativeChecked([string]$CapabilityCode, [string[]]$Command) {
    try {
        if ($Command.Length -eq 0) {
            Fail-Capability $CapabilityCode "no command provided"
        }

        if ($Command.Length -eq 1) {
            & $Command[0] 2>&1 | Out-Host
        } else {
            & $Command[0] @($Command[1..($Command.Length - 1)]) 2>&1 | Out-Host
        }
    } catch {
        Fail-Capability $CapabilityCode $_.Exception.Message
    }

    if ($LASTEXITCODE -ne 0) {
        Fail-Capability $CapabilityCode "exit code $LASTEXITCODE"
    }
}

function Invoke-WithRetry([string]$Description, [scriptblock]$Action, [int]$Attempts = 4) {
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return & $Action
        } catch {
            if ($attempt -ge $Attempts) {
                throw
            }

            $delay = [Math]::Min(30, [Math]::Pow(2, $attempt))
            Write-Warning "$Description failed on attempt $attempt/${Attempts}: $($_.Exception.Message). Retrying in $delay seconds."
            Start-Sleep -Seconds $delay
        }
    }
}

function Install-BcContainerHelperModule {
    $availableModule = Get-Module -ListAvailable -Name BcContainerHelper |
        Sort-Object Version -Descending |
        Select-Object -First 1
    if ($null -ne $availableModule) {
        Import-Module BcContainerHelper -Force
        return
    }

    try {
        Invoke-WithRetry "Install NuGet package provider" {
            Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser -ErrorAction Stop | Out-Null
        }
        Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
        Invoke-WithRetry "Install BcContainerHelper from PSGallery" {
            Install-Module BcContainerHelper -Force -AllowClobber -Scope CurrentUser -Repository PSGallery -ErrorAction Stop
        }
    } catch {
        Write-Warning "Install-Module failed: $($_.Exception.Message). Falling back to direct package download."
        $moduleRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "PowerShell\Modules\BcContainerHelper"
        $packagePath = Join-Path $env:RUNNER_TEMP "BcContainerHelper.nupkg"
        $extractPath = Join-Path $env:RUNNER_TEMP "BcContainerHelper"
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $moduleRoot, $extractPath, $packagePath
        New-Item -ItemType Directory -Force -Path $moduleRoot | Out-Null
        Invoke-WithRetry "Download BcContainerHelper.nupkg" {
            Invoke-WebRequest `
                -Uri "https://www.powershellgallery.com/api/v2/package/BcContainerHelper" `
                -Headers @{ "User-Agent" = "MsDyn365Bc.On.Linux parity workflow" } `
                -MaximumRedirection 5 `
                -OutFile $packagePath `
                -ErrorAction Stop
        }
        Expand-Archive -Path $packagePath -DestinationPath $extractPath -Force
        Copy-Item -Path (Join-Path $extractPath "*") -Destination $moduleRoot -Recurse -Force
    }

    Import-Module BcContainerHelper -Force
}

function Write-FailedTestSummary([string]$TestLog, [string]$Message) {
    "Test codeunits: 70000,70001,70003" | Set-Content -Path $TestLog
    "total=$expectedTests passed=0 failed=$expectedTests skipped=0" | Add-Content -Path $TestLog
    if ($Message) {
        $Message | Add-Content -Path $TestLog
    }
}

function Write-TestSummaryFromXUnit([string]$XUnitPath, [string]$TestLog) {
    "Test codeunits: 70000,70001,70003" | Set-Content -Path $TestLog
    [xml]$xunit = Get-Content -Path $XUnitPath
    $assemblies = @($xunit.assemblies.assembly)
    if ($assemblies.Count -eq 0) {
        throw "XUnit result file contains no assembly nodes: $XUnitPath"
    }

    $total = 0
    $passed = 0
    $failed = 0
    $skipped = 0
    foreach ($assembly in $assemblies) {
        $total += [int]$assembly.total
        $passed += [int]$assembly.passed
        $failed += [int]$assembly.failed
        if ($null -ne $assembly.skipped -and "$($assembly.skipped)" -ne "") {
            $skipped += [int]$assembly.skipped
        }
    }

    "total=$total passed=$passed failed=$failed skipped=$skipped" | Add-Content -Path $TestLog
    return @{ Total = $total; Passed = $passed; Failed = $failed; Skipped = $skipped }
}

function Start-DockerServiceIfPresent {
    $dockerService = Get-Service -Name docker -ErrorAction SilentlyContinue
    if ($null -ne $dockerService -and $dockerService.Status -ne "Running") {
        Start-Service -Name docker
        $dockerService.WaitForStatus("Running", [TimeSpan]::FromSeconds(60))
    }
}

Start-DockerServiceIfPresent
Invoke-NativeChecked "WINDOWS_RUNNER_DOCKER_UNAVAILABLE" @("docker", "version")
Invoke-NativeChecked "WINDOWS_RUNNER_WINDOWS_CONTAINERS_UNAVAILABLE" @("docker", "run", "--rm", "mcr.microsoft.com/windows/nanoserver:ltsc2022", "cmd", "/c", "ver")

$dockerServerVersion = "unknown"
try {
    $dockerVersionOutput = & docker version --format '{{.Server.Version}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $dockerVersionOutput) {
        $dockerServerVersion = ($dockerVersionOutput | Select-Object -First 1)
    }
} catch {
    $dockerServerVersion = "unknown"
}

try {
    Install-BcContainerHelperModule
    Get-Module BcContainerHelper | Format-List Name,Version | Out-Host
} catch {
    Fail-Capability "BC_CONTAINER_HELPER_UNAVAILABLE" $_.Exception.Message
}

$securePassword = ConvertTo-SecureString $Password -AsPlainText -Force
$credential = [pscredential]::new($Username, $securePassword)

try {
    $artifactUrl = Get-BCArtifactUrl -Type OnPrem -Country w1 -Version $BcVersion
    $additionalParameters = @(
        "--publish", "7045:7086",
        "--publish", "7046:7046",
        "--publish", "7047:7047",
        "--publish", "7048:7048",
        "--publish", "7049:7049",
        "--publish", "7052:7048",
        "--publish", "7085:80",
        "--publish", "7086:7086"
    )
    New-BcContainer `
        -accept_eula `
        -containerName $ContainerName `
        -artifactUrl $artifactUrl `
        -Credential $credential `
        -auth UserPassword `
        -isolation process `
        -updateHosts `
        -includeTestRunnerOnly `
        -shortcuts None `
        -additionalParameters $additionalParameters
} catch {
    Fail-Capability "WINDOWS_BC_CONTAINER_START_FAILED" $_.Exception.Message
}

$runnerTemp = $env:RUNNER_TEMP
if (-not $runnerTemp) {
    $runnerTemp = [IO.Path]::GetTempPath()
}

$testLog = Join-Path $runnerTemp "bc-parity-tests-$BcVersion.log"
$bcContainerHelperData = Join-Path $env:ProgramData "BcContainerHelper"
New-Item -ItemType Directory -Force -Path $bcContainerHelperData | Out-Null
$xunitPath = Join-Path $bcContainerHelperData "bc-parity-$BcVersion.xml"
$workspaceXunitPath = Join-Path $contractsDir "bc-parity-$BcVersion.xml"
$testStatus = 0
try {
    Publish-BcContainerApp -containerName $ContainerName -appFile $SmokeAppPath -sync -install -skipVerification
    if (Test-Path -Path $xunitPath) {
        Remove-Item -Path $xunitPath -Force
    }

    $allPassed = Run-TestsInBcContainer `
        -containerName $ContainerName `
        -credential $credential `
        -extensionId "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" `
        -testCodeunitRange "70000|70001|70003" `
        -detailed `
        -XUnitResultFileName $xunitPath `
        -ReturnTrueIfAllPassed

    try {
        $summary = Write-TestSummaryFromXUnit -XUnitPath $xunitPath -TestLog $testLog
    } catch {
        $testStatus = 1
        Write-FailedTestSummary -TestLog $testLog -Message "XUnit result file missing or unparseable: $($_.Exception.Message)"
        $summary = $null
    }

    if ($null -ne $summary) {
        if (-not $allPassed) {
            $testStatus = 1
            "Run-TestsInBcContainer returned false" | Add-Content -Path $testLog
        } elseif ($summary.Total -ne $expectedTests) {
            $testStatus = 1
            "Expected $expectedTests tests in XUnit result, found $($summary.Total): $xunitPath" | Add-Content -Path $testLog
        } elseif ($summary.Failed -ne 0) {
            $testStatus = 1
            "XUnit result reported $($summary.Failed) failed tests" | Add-Content -Path $testLog
        }
    }
    if (Test-Path -Path $xunitPath) {
        Copy-Item -Path $xunitPath -Destination $workspaceXunitPath -Force
    }
} catch {
    $testStatus = 1
    $runnerError = $_.Exception.Message
    if (Test-Path -Path $xunitPath) {
        try {
            $null = Write-TestSummaryFromXUnit -XUnitPath $xunitPath -TestLog $testLog
            $runnerError | Add-Content -Path $testLog
        } catch {
            Write-FailedTestSummary -TestLog $testLog -Message $runnerError
        }
    } else {
        Write-FailedTestSummary -TestLog $testLog -Message $runnerError
    }
    if (Test-Path -Path $xunitPath) {
        Copy-Item -Path $xunitPath -Destination $workspaceXunitPath -Force
    }
}

try {
    $permissionsMockApps = @(
        Get-BcContainerAppInfo -containerName $ContainerName -tenant default -tenantSpecificProperties |
            Where-Object { $_.Publisher -eq "Microsoft" -and $_.Name -eq "Permissions Mock" }
    )
    foreach ($app in $permissionsMockApps) {
        UnPublish-BcContainerApp `
            -containerName $ContainerName `
            -publisher $app.Publisher `
            -name $app.Name `
            -version $app.Version `
            -unInstall `
            -doNotSaveData `
            -doNotSaveSchema `
            -force
    }
} catch {
    Fail-Capability "WINDOWS_TEST_ONLY_APP_CLEANUP_FAILED" $_.Exception.Message
}

try {
    & python "$repoRoot\parity\collect_contract.py" `
        --platform windows `
        --bc-version $BcVersion `
        --base-url "http://localhost:7046/BC" `
        --management-url "http://localhost:7045/BC/Management" `
        --management-api-url "http://localhost:7086/BC/managementApi/v1.0/companies" `
        --soap-url "http://localhost:7047/BC/WS/Services" `
        --web-client-url "http://localhost:7085/BC/" `
        --client-websocket-url "http://localhost:7085/BC/client/csh" `
        --dev-url "http://localhost:7049/BC/dev" `
        --odata-url "http://localhost:7048/BC/ODataV4" `
        --api-url "http://localhost:7052/BC/api/v2.0" `
        --auth "$Username`:$Password" `
        --invalid-auth "not-admin:not-admin" `
        --test-output $testLog `
        --runner-kind bccontainerhelper `
        --diagnostic "artifactUrl=$artifactUrl" `
        --diagnostic "bcContainerHelper=$((Get-Module BcContainerHelper).Version)" `
        --diagnostic "dockerServerVersion=$dockerServerVersion" `
        --out $OutJson
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}

$collectorStatus = $LASTEXITCODE
if ($collectorStatus -ne 0) {
    exit $collectorStatus
}

exit $testStatus
