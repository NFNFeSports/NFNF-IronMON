$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$Plat = "windows-x64"

Write-Host "=== NFNF IronMON Windows build ==="
Write-Host "Root: $Root"
Write-Host "Platform: $Plat"

if (-not (Test-Path "runtime\java\windows-x64\bin\java.exe")) {
    throw "Missing Windows Java runtime"
}

if (-not (Test-Path "runtime\sdl2\windows-x64\SDL2.dll")) {
    throw "Missing Windows SDL2.dll"
}

if (-not (Test-Path "emulator\cores\windows-x64\mgba_libretro.dll")) {
    throw "Missing Windows mGBA libretro core"
}

if (-not (Test-Path "build\jdk\windows-x64\bin\jlink.exe")) {
    throw "Missing Windows jlink.exe"
}

Write-Host "All Windows components present."
