<#
.SYNOPSIS
    Prepares a freshly imaged Raspberry Pi OS (Trixie/Bookworm) bootfs partition on Windows.
.DESCRIPTION
    - Auto-detects the SD card 'bootfs' drive (or uses -BootDrive).
    - Updates cmdline.txt: ensures 'console=serial0,115200' is present and removes 'resize' (unless -KeepResize).
    - Updates user-data: adds 'growpart: { mode: off }' (unless -KeepResize) so cloud-init leaves room for setup_pi.sh to size rootfs + /data.
    - Updates config.txt: ensures '[all]' contains enable_uart=1, dtparam=spi=on, and dtparam=i2c_arm=on.
#>
[CmdletBinding()]
param(
    [string]$BootDrive = "",
    [switch]$KeepResize
)

$ErrorActionPreference = "Stop"

function Write-Utf8NoBomLf {
    param([string]$Path, [string]$Text)
    $normalized = ($Text -replace "`r`n", "`n") -replace "`r", "`n"
    if (-not $normalized.EndsWith("`n")) {
        $normalized += "`n"
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $normalized, $enc)
}

if (-not $BootDrive) {
    $candidates = Get-CimInstance Win32_LogicalDisk | Where-Object {
        $_.VolumeName -eq "bootfs" -or ($_.DriveType -eq 2 -and (Test-Path "$($_.DeviceID)\cmdline.txt") -and (Test-Path "$($_.DeviceID)\config.txt"))
    }
    if (-not $candidates) {
        throw "Could not auto-detect Raspberry Pi 'bootfs' drive. Pass -BootDrive (e.g. -BootDrive D)."
    }
    if (@($candidates).Count -gt 1) {
        throw "Multiple bootfs candidates found. Pass -BootDrive explicitly."
    }
    $BootDrive = @($candidates)[0].DeviceID
}

$BootDrive = $BootDrive.TrimEnd(":", "\") + ":"
$BootRoot = "$BootDrive\"

$CmdlinePath = Join-Path $BootRoot "cmdline.txt"
$ConfigPath = Join-Path $BootRoot "config.txt"
$UserDataPath = Join-Path $BootRoot "user-data"

if (-not (Test-Path $CmdlinePath) -or -not (Test-Path $ConfigPath)) {
    throw "Drive $BootRoot does not look like a Raspberry Pi bootfs partition (missing cmdline.txt or config.txt)."
}

Write-Host "Preparing Raspberry Pi boot partition at $BootRoot ..." -ForegroundColor Cyan

# 1. Update cmdline.txt (single line, LF)
$rawCmdline = [System.IO.File]::ReadAllText($CmdlinePath).Trim()
$tokens = @($rawCmdline -split "\s+" | Where-Object { $_ -ne "" })
if (-not $KeepResize) {
    $tokens = @($tokens | Where-Object { $_ -ne "resize" })
}
if ($tokens -notcontains "console=serial0,115200") {
    $tokens = @("console=serial0,115200") + $tokens
}
Write-Utf8NoBomLf -Path $CmdlinePath -Text ($tokens -join " ")
Write-Host "  [OK] cmdline.txt updated: $($tokens -join ' ')"

# 2. Update user-data (disable cloud-init growpart unless -KeepResize)
if ((Test-Path $UserDataPath) -and (-not $KeepResize)) {
    $rawUserData = [System.IO.File]::ReadAllText($UserDataPath)
    if ($rawUserData -notmatch "(?m)^growpart:") {
        $trimmed = ($rawUserData -replace "`r`n", "`n").TrimEnd("`n")
        $newUserData = $trimmed + "`n`ngrowpart:`n  mode: off`n"
        Write-Utf8NoBomLf -Path $UserDataPath -Text $newUserData
        Write-Host "  [OK] user-data updated: disabled cloud-init growpart"
    } else {
        Write-Host "  [OK] user-data already configures growpart"
    }
}

# 3. Update config.txt under a trailing [all] section so model filters ([pi5], [cm5], etc.) never mask it
$rawConfig = ([System.IO.File]::ReadAllText($ConfigPath) -replace "`r`n", "`n")
$lines = [System.Collections.Generic.List[string]]::new()
foreach ($line in ($rawConfig -split "`n")) {
    $lines.Add($line)
}

# Remove trailing empty lines
while ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -eq "") {
    $lines.RemoveAt($lines.Count - 1)
}

# Find the last section header like [all], [pi5], [cm4], etc.
$lastSectionIdx = -1
$lastSectionName = ""
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Trim() -match "^\[([^\]]+)\]$") {
        $lastSectionIdx = $i
        $lastSectionName = $Matches[1].ToLowerInvariant()
    }
}

if ($lastSectionName -ne "all") {
    $lines.Add("")
    $lines.Add("[all]")
    $lastSectionIdx = $lines.Count - 1
}

$requiredParams = @(
    @{ Key = "enable_uart"; Line = "enable_uart=1" },
    @{ Key = "dtparam=spi"; Line = "dtparam=spi=on" },
    @{ Key = "dtparam=i2c_arm"; Line = "dtparam=i2c_arm=on" }
)

foreach ($param in $requiredParams) {
    $foundInAll = $false
    for ($i = $lastSectionIdx + 1; $i -lt $lines.Count; $i++) {
        $trimmed = $lines[$i].Trim()
        if ($trimmed -eq $param.Line) {
            $foundInAll = $true
            break
        }
        if ($trimmed -match ("^#?\s*" + [regex]::Escape($param.Key) + "=")) {
            $lines[$i] = $param.Line
            $foundInAll = $true
            break
        }
    }
    if (-not $foundInAll) {
        $lines.Add($param.Line)
    }
}

Write-Utf8NoBomLf -Path $ConfigPath -Text ($lines -join "`n")
Write-Host "  [OK] config.txt updated under [all]: enable_uart=1, dtparam=spi=on, dtparam=i2c_arm=on"

Write-Host ""
Write-Host "Done! Safely eject $BootDrive, insert the SD card into your Pi Zero W, and power on." -ForegroundColor Green
