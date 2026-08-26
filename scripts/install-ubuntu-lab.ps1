[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "check-v-drive.ps1") -Mode startup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$distroName = "Ubuntu-Quiz-Lab"
$installLocation = "V:\WSL\Ubuntu-Quiz-Lab"
$installed = @(
    wsl --list --quiet |
        ForEach-Object { ($_ -replace "`0", "").Trim() } |
        Where-Object { $_ }
)
if ($installed -contains $distroName) {
    throw "$distroName is already registered."
}
if ((Test-Path -LiteralPath $installLocation) -and (Get-ChildItem -Force $installLocation)) {
    throw "$installLocation is not empty. Refusing to overwrite it."
}

wsl --install Ubuntu-24.04 `
    --location $installLocation `
    --name $distroName `
    --vhd-size 160GB `
    --no-launch `
    --web-download
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Installed $distroName at $installLocation. Launch it once to create the Linux user."
