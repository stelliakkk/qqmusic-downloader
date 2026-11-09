$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $project
$env:TEMP = Join-Path $project '.cache\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $project '.cache\pip'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $project '.cache\pyinstaller'
$python = Join-Path $project '.venv\Scripts\python.exe'
$versionLine = Select-String -LiteralPath 'src\qq_downloader\__init__.py' -Pattern '^__version__ = "(\d+\.\d+\.\d+)"$'
if (-not $versionLine) { throw 'Unable to read the application version.' }
$version = $versionLine.Matches[0].Groups[1].Value
$staging = Join-Path $project "build\stage-v$version"
$bundle = Join-Path $staging 'QQMusicDownloader'

# PyInstaller replaces its own staging tree. Refuse redirects outside the project.
foreach ($dir in @((Join-Path $project 'build'), $staging, $bundle)) {
    if ((Test-Path -LiteralPath $dir) -and (Get-Item -LiteralPath $dir -Force).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
        throw "Build path is a reparse point: $dir"
    }
}
New-Item -ItemType Directory -Force -Path $env:TEMP,'build',$staging | Out-Null
$env:QT_QPA_PLATFORM = 'offscreen'
$icon = Join-Path $project 'build\qqmusic.ico'
$basePython = & $python -c 'import sys; print(sys.base_prefix)'
if ($LASTEXITCODE -ne 0) { throw 'Unable to locate the build interpreter.' }
# Limit native dependency discovery to Windows and the selected Python.
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$basePython"
& $python -X utf8 scripts\build_icon.py $icon
if ($LASTEXITCODE -ne 0) { throw 'Icon build failed.' }
& $python -X utf8 -m PyInstaller --noconfirm --onedir --windowed --noupx `
    --name QQMusicDownloader --icon $icon --distpath $staging `
    --workpath "build\pyinstaller-v$version" --specpath build --paths src src\qq_music_app.py
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }

$internal = Join-Path $bundle '_internal'
# Use the Windows ICU implementation and the native Qt Widgets renderer.
Get-ChildItem -LiteralPath $internal -File | Where-Object Name -Match '^icu.*\.dll$' | Remove-Item -Force
$qt = Join-Path $internal 'PySide6'
$optional = @('opengl32sw.dll', 'plugins\imageformats\qicns.dll', 'plugins\imageformats\qtga.dll', `
    'plugins\imageformats\qtiff.dll', 'plugins\imageformats\qwbmp.dll', 'plugins\imageformats\qwebp.dll')
foreach ($name in $optional) {
    $path = Join-Path $qt $name
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
}
if (Test-Path -LiteralPath "$qt\translations" -PathType Container) {
    Get-ChildItem -LiteralPath "$qt\translations" -Filter '*.qm' -File | Remove-Item -Force
}
foreach ($name in @('data', 'downloads', 'runtime', 'reports')) {
    if (Test-Path -LiteralPath (Join-Path $bundle $name)) { throw "Unexpected mutable directory in bundle: $name" }
}
Write-Output (Join-Path $bundle 'QQMusicDownloader.exe')
