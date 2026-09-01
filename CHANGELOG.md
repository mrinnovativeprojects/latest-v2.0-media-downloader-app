## 2.4.0
- Added self-healing FFmpeg validation when ClipForge opens.
- Automatically replaces incomplete/shared FFmpeg installs with a complete Essentials bin folder, including required DLLs.
- Fixed the `avformat-63.dll` style startup/processing failure caused by copying only `ffmpeg.exe`.
- Audio extraction now pins FFmpeg to the app-local runtime and explicitly disables video in the audio postprocessor.
- Upgraded the bundled yt-dlp requirement to the current stable 2026.08.19 line to improve site compatibility.
- Rebuilt package collection to include imageio-ffmpeg fallback components.

## 2.3.6
- Removed Open File and Open Folder controls and their backend endpoints as requested.

# ClipForge 2.3.5

- Removed startup-time `imageio-ffmpeg` import; FFmpeg fallback is now initialized only when a download actually needs it.
- Reworked progress rendering with requestAnimationFrame + exponential smoothing so the percentage/bar moves continuously instead of fighting CSS transitions.
- Added continuous live speed/ETA projection between backend progress events.
- Removed HTTP range chunking overhead and raised fragmented-download concurrency to 16 for better throughput where the source permits it.
- Hardened exact Windows Open File/Open Folder launching with native ShellExecuteW and fallback handling.

# ClipForge 2.3.4

- Reworked live download progress interpolation so percentage, bar fill and ETA update smoothly without polling resets.
- Added animated progress-bar shine/flow and live-speed pulse.
- Fixed Windows file/folder opening using native Windows associations with ShellExecute fallback.
- Reduced backend startup wait to improve launch responsiveness.

## 2.3.2
- Fixed Open file to use the exact Windows file association and Open verb.
- Fixed Open folder to launch the exact selected destination in Windows Explorer.
- Added a rename-before-download dialog with a safe Windows filename.
- Custom names are preserved for completed files and retries.

# Changelog

## 2.2.0
- Replaced the Python/PowerShell runtime launcher with a real Windows GUI executable built by PyInstaller (`--noconsole`) so normal launches do not show a console window.
- Embedded the ClipForge icon in the executable for consistent taskbar/window identity.
- Added both Desktop and Start Menu shortcuts.
- Added automatic rebuild/upgrade detection for the native executable.
- Hardened Open File/Open Folder path handling.
- Improved exact MP4/MKV/WebM selection and final-file resolution.


## 2.1.0
- Removed normal-launch PowerShell/shortcut-repair delay.
- Added a single-instance guard so repeated clicks do not create another ClipForge window.
- Upgraded pywebview for Windows application icon support.
- Fixed Open file/Open folder shell handling and final-output path resolution after FFmpeg post-processing.
- Improved MP4/WebM/MKV format selection and fallbacks.


## 1.1.4
- Fixed the Windows taskbar/application icon so ClipForge no longer uses the Python icon.
- Added the ClipForge icon directly to pywebview's Windows window startup.
- Added a stable Windows AppUserModelID for correct taskbar grouping.
- Added a stronger FFmpeg installation path using the official Gyan.dev Windows Essentials ZIP, with WinGet and imageio-ffmpeg fallbacks.
- Added imageio-ffmpeg as an installed fallback dependency.
- Existing app UI files were not changed.
- Re-running the installer now performs an upgrade/repair instead of simply launching the old installation.

## 1.1.1
- Fixed first-run elevation and normal post-install launching.
- Fixed shortcut creation/runtime targeting.
- Added installation-complete marker.
- Improved FFmpeg/ffprobe detection.
- Added fallback ports if the preferred local port is busy.
- Added a visible startup error dialog.
- Existing app UI was not modified.

## 2.3.0
- Reduced GUI startup delay by bootstrapping the Flask backend and pywebview import in parallel.
- Removed the startup HTTP health-check wait from the desktop launcher.
- Enabled 8 concurrent fragment downloads for faster segmented media downloads.
- Increased the download read buffer to 1 MiB.
- Enabled 10 MiB HTTP range chunks for YouTube direct HTTP downloads to help mitigate request throttling.

## 2.3.1
- Faster cold startup by lazy-loading yt-dlp.
- Stable taskbar AppUserModelID for pinned/running icon grouping.
- More responsive downloads with higher fragment concurrency, larger buffers and chunking.
- Smoothed real-time speed/percentage/ETA telemetry.
- More reliable Windows Open File/Open Folder behavior.

## 2.3.3
- Reduced desktop launch waiting and kept backend/GUI startup parallel.
- Removed forced video re-encoding so compatible downloads finish much faster.
- Aggregated video/audio stream progress so the progress bar does not reset between streams.
- Added live byte/time speed calculation, smoother ETA and progress updates.
- Added client-side progress interpolation for smoother real-time percentage and ETA display.
- Hardened Windows Open File/Open Folder behavior with native ShellExecuteW/Explorer handling.
