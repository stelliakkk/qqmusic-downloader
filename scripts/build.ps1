$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $project
$env:TEMP = Join-Path $project '.cache\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $project '.cache\pip'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $project '.cache\pyinstaller'
$python = Join-Path $project '.venv\Scripts\python.exe'
$app = Join-Path $project 'dist\QQMusicDownloader'
$staging = Join-Path $project 'build\stage'
$mediaTools = @('ffmpeg.exe', 'ffprobe.exe')
foreach ($tool in $mediaTools) {
    if (-not (Test-Path -LiteralPath (Join-Path $project "runtime\$tool") -PathType Leaf)) {
        throw "Place $tool in the project runtime directory before building."
    }
}
New-Item -ItemType Directory -Force -Path $env:TEMP,'build',$app | Out-Null
$env:QT_QPA_PLATFORM = 'offscreen'
$icon = Join-Path $project 'build\qqmusic.ico'
& $python -X utf8 scripts\build_icon.py $icon
if ($LASTEXITCODE -ne 0) { throw 'Icon build failed.' }
& $python -X utf8 -m PyInstaller --noconfirm --onedir --windowed --noupx `
    --name QQMusicDownloader --icon $icon --distpath $staging `
    --workpath build\pyinstaller --specpath build --paths src src\qq_music_app.py
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
$bundle = Join-Path $staging 'QQMusicDownloader'
# Qt uses the Windows ICU exports. A foreign ICU from PATH shadows them and breaks startup.
$icuBackup = Join-Path $project 'build\icu-shadow'
New-Item -ItemType Directory -Force -Path $icuBackup | Out-Null
foreach ($location in @($bundle, $app)) {
    foreach ($name in @('icuuc.dll', 'icudt78.dll')) {
        $file = Join-Path $location "_internal\$name"
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            Move-Item -LiteralPath $file -Destination $icuBackup -Force
        }
    }
}
Copy-Item -LiteralPath (Join-Path $bundle 'QQMusicDownloader.exe') -Destination $app -Force
Copy-Item -LiteralPath (Join-Path $bundle '_internal') -Destination $app -Force -Recurse
New-Item -ItemType Directory -Force -Path "$app\runtime","$app\downloads","$app\data" | Out-Null
foreach ($tool in $mediaTools) {
    Copy-Item -LiteralPath "runtime\$tool" -Destination "$app\runtime" -Force
}
Write-Output (Join-Path $app 'QQMusicDownloader.exe')
