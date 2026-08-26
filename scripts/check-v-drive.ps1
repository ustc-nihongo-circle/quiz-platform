[CmdletBinding()]
param(
    [ValidateSet("startup", "removal")]
    [string]$Mode = "startup",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $ConfigPath) {
    $ConfigPath = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "_private\deployment-local\v-drive.json"
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Missing private V: identity config: $ConfigPath"
}
$driveConfig = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$expectedLabel = $driveConfig.volume_label
$expectedSerial = $driveConfig.volume_serial
$volume = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='V:'"
if (-not $volume) { throw "V: is not mounted." }
if ($volume.VolumeName -ne $expectedLabel -or $volume.VolumeSerialNumber -ne $expectedSerial) {
    throw "V: does not match the expected private volume identity."
}

$paths = @(
    "V:\DockerDesktopWSL",
    "V:\WSL",
    "V:\WSL\Ubuntu-Quiz-Lab"
)
$pathState = foreach ($path in $paths) {
    [pscustomobject]@{ Path = $path; Exists = Test-Path -LiteralPath $path }
}
$runningWsl = @(
    wsl --list --running --quiet |
        ForEach-Object { ($_ -replace "`0", "").Trim() } |
        Where-Object { $_ }
)
$processes = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'docker|vmmem' } | Select-Object ProcessName, Id)

[pscustomobject]@{
    Mode = $Mode
    Volume = $volume.DeviceID
    Label = $volume.VolumeName
    Serial = $volume.VolumeSerialNumber
    FreeGiB = [math]::Round($volume.FreeSpace / 1GB, 1)
    RunningWsl = $runningWsl -join ", "
    DockerOrWslProcessCount = $processes.Count
} | Format-List
$pathState | Format-Table -AutoSize

if ($Mode -eq "removal" -and ($runningWsl.Count -gt 0 -or $processes.Count -gt 0)) {
    Write-Error "V: is not ready for removal. Stop Ubuntu services, quit Docker Desktop, run wsl --shutdown, then check again."
    exit 2
}

if ($Mode -eq "removal") {
    Write-Output "V: passed the read-only removal preflight. Use Windows safe removal or set the verified disk offline before unplugging."
} else {
    Write-Output "V: passed the startup preflight. Start only the environment you intend to use, then run its health checks."
}
