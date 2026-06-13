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

Invoke-NativeChecked "WINDOWS_RUNNER_DOCKER_UNAVAILABLE" @("docker", "version")
Invoke-NativeChecked "WINDOWS_RUNNER_WINDOWS_CONTAINERS_UNAVAILABLE" @("docker", "run", "--rm", "mcr.microsoft.com/windows/nanoserver:ltsc2022", "cmd", "/c", "ver")

try {
    Install-PackageProvider -Name NuGet -Force | Out-Null
    Install-Module BcContainerHelper -Force -AllowClobber
    Import-Module BcContainerHelper
    Get-Module BcContainerHelper | Format-List Name,Version | Out-Host
} catch {
    Fail-Capability "BC_CONTAINER_HELPER_UNAVAILABLE" $_.Exception.Message
}

$securePassword = ConvertTo-SecureString $Password -AsPlainText -Force
$credential = [pscredential]::new($Username, $securePassword)
$artifactUrl = Get-BCArtifactUrl -Type OnPrem -Country w1 -Version $BcVersion

try {
    New-BcContainer `
        -accept_eula `
        -containerName $ContainerName `
        -artifactUrl $artifactUrl `
        -Credential $credential `
        -auth UserPassword `
        -isolation process `
        -updateHosts `
        -shortcuts None
} catch {
    Fail-Capability "WINDOWS_BC_CONTAINER_START_FAILED" $_.Exception.Message
}

$testLog = Join-Path $env:RUNNER_TEMP "bc-parity-tests-$BcVersion.log"
try {
    Publish-BcContainerApp -containerName $ContainerName -appFile $SmokeAppPath -sync -install -skipVerification
    $results = Run-TestsInBcContainer -containerName $ContainerName -credential $credential -extensionId "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" -XUnitResultFileName (Join-Path $env:RUNNER_TEMP "bc-parity-$BcVersion.xml")
    $results | Out-String | Tee-Object -FilePath $testLog
} catch {
    "Test codeunits: 70000,70001" | Set-Content -Path $testLog
    "total=0 passed=0 failed=1 skipped=0" | Add-Content -Path $testLog
    $_.Exception.Message | Add-Content -Path $testLog
}

python "$repoRoot\parity\collect_contract.py" `
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
    --out $OutJson
