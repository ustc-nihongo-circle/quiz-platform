[CmdletBinding()]
param(
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repositoryRoot
try {
    $status = git status --porcelain
    if ($LASTEXITCODE -ne 0) { throw "git status failed" }
    if ($status) { throw "The working tree must be clean before building a release." }
    if (git ls-files _private docs-agent media var) {
        throw "Private or runtime paths are tracked. Refusing to build a release."
    }

    $commit = (git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Unable to resolve HEAD." }
    $shortCommit = $commit.Substring(0, 12)
    $outputRoot = Join-Path $repositoryRoot $OutputDirectory
    New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
    $archive = Join-Path $outputRoot "nihongo-quiz-$shortCommit.zip"
    git archive --format=zip --output=$archive HEAD
    if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

    # A Windows Git checkout can otherwise export executable shell scripts with
    # CRLF line endings. systemd then sees `/usr/bin/env bash\r` and refuses to
    # start the timer or service, even though `bash script.sh` worked manually.
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries | Where-Object { $_.FullName -like "deploy/scripts/*.sh" }) {
            $reader = [System.IO.StreamReader]::new($entry.Open(), [System.Text.Encoding]::UTF8, $true)
            try {
                $contents = $reader.ReadToEnd()
            } finally {
                $reader.Dispose()
            }
            if ($contents.Contains("`r`n")) {
                throw "Release archive contains CRLF shell script: $($entry.FullName)"
            }
        }
    } finally {
        $zip.Dispose()
    }

    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    $manifest = [ordered]@{
        release_id = $shortCommit
        commit = $commit
        created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        archive = Split-Path -Leaf $archive
        archive_sha256 = $archiveHash
        pyproject_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath "pyproject.toml").Hash.ToLowerInvariant()
        migrations = @(git ls-files "src/quiz/migrations/*.py")
    }
    $manifestPath = Join-Path $outputRoot "nihongo-quiz-$shortCommit.manifest.json"
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $manifestPath
    Write-Output $archive
    Write-Output $manifestPath
} finally {
    Pop-Location
}
