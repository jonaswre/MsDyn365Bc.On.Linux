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

function Write-FailedTestSummary([string]$TestLog, [string]$Message) {
    "Test codeunits: 70000,70001" | Set-Content -Path $TestLog
    "total=4 passed=0 failed=4 skipped=0" | Add-Content -Path $TestLog
    if ($Message) {
        $Message | Add-Content -Path $TestLog
    }
}

function Write-TestSummaryFromXUnit([string]$XUnitPath, [string]$TestLog) {
    "Test codeunits: 70000,70001" | Set-Content -Path $TestLog
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
    try {
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser | Out-Null
        Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
        Install-Module BcContainerHelper -Force -AllowClobber -Scope CurrentUser -Repository PSGallery
    } catch {
        $moduleRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "PowerShell\Modules\BcContainerHelper"
        $packagePath = Join-Path $env:RUNNER_TEMP "BcContainerHelper.nupkg"
        $extractPath = Join-Path $env:RUNNER_TEMP "BcContainerHelper"
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $moduleRoot, $extractPath, $packagePath
        New-Item -ItemType Directory -Force -Path $moduleRoot | Out-Null
        Invoke-WebRequest `
            -Uri "https://www.powershellgallery.com/api/v2/package/BcContainerHelper" `
            -OutFile $packagePath
        Expand-Archive -Path $packagePath -DestinationPath $extractPath -Force
        Copy-Item -Path (Join-Path $extractPath "*") -Destination $moduleRoot -Recurse -Force
    }
    Import-Module BcContainerHelper -Force
    Get-Module BcContainerHelper | Format-List Name,Version | Out-Host
} catch {
    Fail-Capability "BC_CONTAINER_HELPER_UNAVAILABLE" $_.Exception.Message
}

$securePassword = ConvertTo-SecureString $Password -AsPlainText -Force
$credential = [pscredential]::new($Username, $securePassword)

try {
    $artifactUrl = Get-BCArtifactUrl -Type OnPrem -Country w1 -Version $BcVersion
    New-BcContainer `
        -accept_eula `
        -containerName $ContainerName `
        -artifactUrl $artifactUrl `
        -Credential $credential `
        -auth UserPassword `
        -isolation process `
        -updateHosts `
        -includeTestToolkit `
        -shortcuts None
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
        -testCodeunit "70000|70001" `
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
        } elseif ($summary.Total -ne 4) {
            $testStatus = 1
            "Expected 4 tests in XUnit result, found $($summary.Total): $xunitPath" | Add-Content -Path $testLog
        } elseif ($summary.Failed -ne 0) {
            $testStatus = 1
            "XUnit result reported $($summary.Failed) failed tests" | Add-Content -Path $testLog
        }
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
}

try {
    & python "$repoRoot\parity\collect_contract.py" `
        --platform windows `
        --bc-version $BcVersion `
        --base-url "http://localhost:7046/BC" `
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
