param(
    [Parameter(Mandatory = $true)]
    [string]$FolderPath,

    [Parameter(Mandatory = $false)]
    [string]$OutputFile = "output.mp4"
)

$resolvedFolder = Resolve-Path -Path $FolderPath -ErrorAction SilentlyContinue
if (-not $resolvedFolder) {
    Write-Error "Error: '$FolderPath' is not a valid folder."
    exit 1
}

$sourceFolder = $resolvedFolder.Path

if ([System.IO.Path]::IsPathRooted($OutputFile) -or $OutputFile.Contains('\\') -or $OutputFile.Contains('/')) {
    $finalOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputFile)
} else {
    $finalOutput = Join-Path $sourceFolder $OutputFile
}

$outputDir = Split-Path -Parent $finalOutput
if (-not [string]::IsNullOrWhiteSpace($outputDir) -and -not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

$finalOutput = [System.IO.Path]::GetFullPath($finalOutput)

$files = Get-ChildItem -Path $sourceFolder -File |
    Where-Object {
        $_.Extension -ieq ".mp4" -and [System.IO.Path]::GetFullPath($_.FullName) -ne $finalOutput
    } |
    Sort-Object Name

if (-not $files -or $files.Count -eq 0) {
    Write-Error "Error: no .mp4 files found in '$FolderPath' (excluding output file if applicable)."
    exit 1
}

$tmpFile = [System.IO.Path]::GetTempFileName()

try {
    foreach ($file in $files) {
        $escaped = $file.FullName -replace "'", "'\''"
        Add-Content -Path $tmpFile -Value "file '$escaped'"
    }

    Write-Host "Concatenating files from '$sourceFolder' into '$finalOutput'..."

    ffmpeg -f concat -safe 0 -i $tmpFile -c copy $finalOutput

    if ($LASTEXITCODE -ne 0) {
        Write-Error "ffmpeg failed with exit code $LASTEXITCODE."
        exit $LASTEXITCODE
    }

    Write-Host "Done: $finalOutput"
}
finally {
    Remove-Item -Path $tmpFile -ErrorAction SilentlyContinue
}
