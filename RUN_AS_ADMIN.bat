@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "SOURCE=%~dp0"
set "APPNAME=ClipForge"
set "APPVERSION=2.4.0"
set "INSTALLDIR=%LOCALAPPDATA%\ClipForge"
set "LOGDIR=%INSTALLDIR%\logs"
set "LOG=%LOGDIR%\installer.log"
set "MARKER=%INSTALLDIR%\.install-complete"
set "SELF=%~f0"

rem First installation is elevated as requested. Repairs/upgrades run normally.
if not exist "%MARKER%" (
    net session >nul 2>&1
    if errorlevel 1 (
        set "CLIPFORGE_SELF=%SELF%"
        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:CLIPFORGE_SELF -Verb RunAs -WorkingDirectory (Split-Path -Parent $env:CLIPFORGE_SELF)"
        if errorlevel 1 (
            echo Administrator permission was cancelled or could not be granted.
            pause
        )
        exit /b
    )
)

if not exist "%INSTALLDIR%" mkdir "%INSTALLDIR%" >nul 2>&1
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
call :log "============================================================"
call :log "ClipForge installer/repair started"
call :log "Source: %SOURCE%"
call :log "Install location: %INSTALLDIR%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $src=$env:SOURCE.TrimEnd('\'); $dst=$env:INSTALLDIR; $skip=@('.venv','runtime','logs','downloads','data','.install-complete'); New-Item -ItemType Directory -Force -Path $dst | Out-Null; Get-ChildItem -LiteralPath $src -Force | Where-Object { $skip -notcontains $_.Name } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $dst -Recurse -Force -ErrorAction Stop }; exit 0" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_copy
if not exist "%INSTALLDIR%\app.py" goto :fail_copy
if not exist "%INSTALLDIR%\desktop.py" goto :fail_copy
if not exist "%INSTALLDIR%\requirements.txt" goto :fail_copy
if not exist "%INSTALLDIR%\web\index.html" goto :fail_copy

set "PYEXE="
for /f "usebackq delims=" %%P in (`py.exe -3.12 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE for /f "usebackq delims=" %%P in (`py.exe -3 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE for /f "usebackq delims=" %%P in (`python.exe -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE (
    call :log "Python not found. Trying WinGet Python 3.12 install."
    where winget.exe >nul 2>&1
    if errorlevel 1 goto :fail_python
    winget.exe install --id Python.Python.3.12 --exact --source winget --accept-source-agreements --accept-package-agreements --silent --disable-interactivity >>"%LOG%" 2>&1
    call :refresh_path
    for /f "usebackq delims=" %%P in (`py.exe -3.12 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYEXE set "PYEXE=%%P"
)
if not defined PYEXE goto :fail_python
if not exist "%PYEXE%" goto :fail_python
call :log "Python: %PYEXE%"

set "VENV=%INSTALLDIR%\.venv"
set "RUNPY=%VENV%\Scripts\python.exe"
set "RUNPYW=%VENV%\Scripts\pythonw.exe"
if not exist "%RUNPY%" (
    call :log "Creating application virtual environment."
    "%PYEXE%" -m venv "%VENV%" >>"%LOG%" 2>&1
    if errorlevel 1 goto :fail_venv
)
if not exist "%RUNPY%" goto :fail_venv
if not exist "%RUNPYW%" goto :fail_venv

call :log "Installing/repairing application dependencies."
"%RUNPY%" -m ensurepip --upgrade >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_pip
"%RUNPY%" -m pip install --disable-pip-version-check --upgrade pip >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_pip
"%RUNPY%" -m pip install --disable-pip-version-check --upgrade -r "%INSTALLDIR%\requirements.txt" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_dependencies

set "FFMPEGBIN=%INSTALLDIR%\runtime\ffmpeg\bin"
if not exist "%FFMPEGBIN%" mkdir "%FFMPEGBIN%" >nul 2>&1
call :log "Validating bundled FFmpeg runtime."
set "FFMPEG_OK="
for /f "delims=" %%F in ('"%FFMPEGBIN%\ffmpeg.exe" -version 2^>nul ^| findstr /C:"ffmpeg version"') do if not defined FFMPEG_OK set "FFMPEG_OK=1"
if not defined FFMPEG_OK set "FFMPEG_OK="
if defined FFMPEG_OK if not exist "%FFMPEGBIN%\ffprobe.exe" set "FFMPEG_OK="
if not defined FFMPEG_OK (
    call :log "FFmpeg missing or broken. Installing a complete Essentials build including DLLs."
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $bin=$env:FFMPEGBIN; $url='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'; $tmp=Join-Path $env:TEMP ('ClipForge-ffmpeg-' + [guid]::NewGuid().ToString('N') + '.zip'); $extract=Join-Path $env:TEMP ('ClipForge-ffmpeg-' + [guid]::NewGuid().ToString('N')); try { New-Item -ItemType Directory -Force -Path $bin | Out-Null; Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp; Expand-Archive -LiteralPath $tmp -DestinationPath $extract -Force; $src=(Get-ChildItem -LiteralPath $extract -Directory -Recurse | Where-Object { (Test-Path (Join-Path $_.FullName 'ffmpeg.exe')) -and (Test-Path (Join-Path $_.FullName 'ffprobe.exe')) } | Select-Object -First 1); if(-not $src){throw 'FFmpeg archive did not contain a complete bin folder.'}; Get-ChildItem -LiteralPath $bin -Force -ErrorAction SilentlyContinue | Remove-Item -Force -Recurse -ErrorAction SilentlyContinue; Get-ChildItem -LiteralPath $src.FullName -Force | Copy-Item -Destination $bin -Force -Recurse; $env:PATH=$bin+';'+$env:PATH; $a=Start-Process -FilePath (Join-Path $bin 'ffmpeg.exe') -ArgumentList '-version' -WindowStyle Hidden -Wait -PassThru; $b=Start-Process -FilePath (Join-Path $bin 'ffprobe.exe') -ArgumentList '-version' -WindowStyle Hidden -Wait -PassThru; if($a.ExitCode -ne 0 -or $b.ExitCode -ne 0){throw 'Installed FFmpeg/FFprobe failed validation.'}; exit 0 } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue; Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue }" >>"%LOG%" 2>&1
)
if not exist "%FFMPEGBIN%\ffmpeg.exe" (
    call :log "Direct FFmpeg repair failed. Trying imageio-ffmpeg fallback."
    "%RUNPY%" -m pip install --disable-pip-version-check --upgrade imageio-ffmpeg >>"%LOG%" 2>&1
    if not errorlevel 1 (
        for /f "usebackq delims=" %%F in (`"%RUNPY%" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2^>nul`) do if exist "%%F" copy /Y "%%F" "%FFMPEGBIN%\ffmpeg.exe" >nul
    )
)
if not exist "%FFMPEGBIN%\ffmpeg.exe" goto :fail_ffmpeg
if not exist "%FFMPEGBIN%\ffprobe.exe" goto :fail_ffmpeg
set "PATH=%FFMPEGBIN%;%PATH%"
call :log "FFmpeg runtime: %FFMPEGBIN%"

"%RUNPY%" -c "import flask, yt_dlp, webview; print('ClipForge dependency validation OK')" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_validation

rem Build a real Windows GUI executable. This removes Python/PowerShell console
rem windows and gives the application its own embedded ClipForge icon/taskbar identity.
set "APPDIST=%INSTALLDIR%\appdist"
set "APPEXE=%APPDIST%\ClipForge\ClipForge.exe"
if exist "%MARKER%" (
    findstr /C:"Version: %APPVERSION%" "%MARKER%" >nul 2>&1
    if errorlevel 1 if exist "%APPDIST%" rmdir /s /q "%APPDIST%" >>"%LOG%" 2>&1
)
if not exist "%APPEXE%" (
    call :log "Building ClipForge Windows GUI executable."
    "%RUNPY%" -m pip install --disable-pip-version-check --upgrade "pyinstaller>=6.10,<7" >>"%LOG%" 2>&1
    if errorlevel 1 goto :fail_pyinstaller
    if exist "%APPDIST%" rmdir /s /q "%APPDIST%" >>"%LOG%" 2>&1
    if exist "%INSTALLDIR%\build" rmdir /s /q "%INSTALLDIR%\build" >>"%LOG%" 2>&1
    "%RUNPY%" -m PyInstaller --noconfirm --clean --onedir --noconsole --name ClipForge --icon "%INSTALLDIR%\ClipForge.ico" --add-data "%INSTALLDIR%\web;web" --add-data "%INSTALLDIR%\ClipForge.ico;." --collect-all yt_dlp --collect-all webview --collect-all imageio_ffmpeg --hidden-import tkinter --hidden-import tkinter.filedialog --distpath "%APPDIST%" --workpath "%INSTALLDIR%\build" --specpath "%INSTALLDIR%\build" "%INSTALLDIR%\desktop.py" >>"%LOG%" 2>&1
    if errorlevel 1 goto :fail_build
)
if not exist "%APPEXE%" goto :fail_build

rem Keep the FFmpeg runtime beside the executable so it is independent of PATH.
if exist "%INSTALLDIR%\runtime" (
    if exist "%APPDIST%\ClipForge\runtime" rmdir /s /q "%APPDIST%\ClipForge\runtime" >>"%LOG%" 2>&1
    xcopy "%INSTALLDIR%\runtime" "%APPDIST%\ClipForge\runtime\" /E /I /Y /Q >nul
)
copy /Y "%INSTALLDIR%\ClipForge.ico" "%APPDIST%\ClipForge\ClipForge.ico" >nul

set "CLIPFORGE_INSTALL_DIR=%APPDIST%\ClipForge"
set "CLIPFORGE_TARGET=%APPEXE%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALLDIR%\create_shortcuts.ps1" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_shortcut
>"%MARKER%" echo ClipForge installation completed successfully.
>>"%MARKER%" echo Version: %APPVERSION%
call :log "Installation/repair complete. Starting ClipForge Windows GUI executable."
start "" "%APPEXE%"
exit /b 0

:refresh_path
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine')+';'+[Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
exit /b 0
:log
>>"%LOG%" echo [%date% %time%] %~1
echo %~1
exit /b 0
:fail_copy
call :log "ERROR: The application files could not be copied."
goto :fail
:fail_python
call :log "ERROR: Python 3 could not be found or installed."
goto :fail
:fail_venv
call :log "ERROR: The Python environment could not be created."
goto :fail
:fail_pip
call :log "ERROR: pip could not be prepared."
goto :fail
:fail_dependencies
call :log "ERROR: Application dependencies could not be installed."
goto :fail
:fail_ffmpeg
call :log "ERROR: FFmpeg could not be installed."
goto :fail
:fail_validation
call :log "ERROR: Installed dependencies failed validation."
goto :fail
:fail_pyinstaller
call :log "ERROR: PyInstaller could not be installed."
goto :fail
:fail_build
call :log "ERROR: The Windows GUI executable could not be built."
goto :fail
:fail_shortcut
call :log "ERROR: Windows shortcuts could not be created."
goto :fail
:fail
echo.
echo ============================================================
echo CLIPFORGE INSTALLATION FAILED
echo See this log:
echo %LOG%
echo ============================================================
echo.
pause
exit /b 1
