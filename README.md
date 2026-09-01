# ClipForge Media Downloader

ClipForge runs as a native Windows desktop application. The existing app UI is unchanged. Version 2.4.0 adds automatic runtime repair for FFmpeg and a more reliable audio-processing path.

## First run
1. Extract the ZIP completely.
2. Right-click `RUN_AS_ADMIN.bat` and choose **Run as administrator**.
3. The installer copies ClipForge to `%LOCALAPPDATA%\ClipForge`, creates a private Python environment, installs dependencies, and creates Desktop and Start Menu shortcuts.
4. Use the single **ClipForge** Desktop shortcut to launch the app. It starts `pythonw.exe` directly, so no PowerShell or console window is shown.

Administrator permission is needed for the first installation/repair only. After installation, launch **ClipForge** from the Desktop shortcut like any other Windows application. Only one ClipForge instance is allowed at a time.

## Automatic repair
When ClipForge opens, it validates the app-local FFmpeg and FFprobe executables. If FFmpeg is missing or damaged, ClipForge automatically downloads a complete Windows Essentials build, including the required DLL files, and repairs the runtime before a download is processed.

If the application installation itself is damaged, run `RUN_AS_ADMIN.bat` as Administrator again. It repairs the installed files while preserving application data and logs.

## Version 2.4.0
- Repairs missing/broken FFmpeg automatically.
- Includes the complete FFmpeg bin set instead of copying only `ffmpeg.exe`, preventing `avformat-*.dll not found` failures.
- Uses the app-local FFmpeg runtime for audio extraction.
- Bundles current stable yt-dlp 2026.08.19 or newer within the supported 2026 release line.

## Logs
- `%LOCALAPPDATA%\ClipForge\logs\installer.log`
- `%LOCALAPPDATA%\ClipForge\logs\desktop.log`
- `%LOCALAPPDATA%\ClipForge\logs\app.log`

## Important
Use only media you are authorized to download. ClipForge does not bypass DRM, private content, paywalls, authentication, or access controls.

### Shortcut reliability
ClipForge resolves the actual Windows Desktop location (including redirected/OneDrive Desktop folders) and creates one Desktop shortcut. The app does not run shortcut-repair PowerShell on every normal launch.
