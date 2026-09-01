$ErrorActionPreference = 'Stop'
$installDir = $env:CLIPFORGE_INSTALL_DIR
$target = $env:CLIPFORGE_TARGET
if ([string]::IsNullOrWhiteSpace($installDir) -or [string]::IsNullOrWhiteSpace($target)) { throw 'ClipForge shortcut configuration is incomplete.' }
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "ClipForge application was not found: $target" }
$iconPath = Join-Path $installDir 'ClipForge.ico'
if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) { throw 'ClipForge application icon was not found.' }

function Expand-FolderPath([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) { return $null }
    return [Environment]::ExpandEnvironmentVariables($value)
}

$desktop = $null
try {
    $desktop = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -Name Desktop -ErrorAction Stop).Desktop
    $desktop = Expand-FolderPath $desktop
} catch {}
if ([string]::IsNullOrWhiteSpace($desktop)) { $desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop) }
if ([string]::IsNullOrWhiteSpace($desktop)) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\ClipForge'
New-Item -ItemType Directory -Force -Path $desktop,$startMenu | Out-Null

$wsh = New-Object -ComObject WScript.Shell
function New-ClipForgeShortcut([string]$path) {
    $shortcut = $wsh.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = ''
    # Use the packaged EXE itself as the icon source so Windows keeps the
    # embedded ClipForge icon when this shortcut is pinned to the taskbar.
    $shortcut.WorkingDirectory = $installDir
    $shortcut.Description = 'ClipForge Media Downloader'
    $shortcut.IconLocation = "$target,0"
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Shortcut was not created: $path" }
    $verify = $wsh.CreateShortcut($path)
    if ($verify.TargetPath -ne $target) { throw "Shortcut target verification failed: $path" }
}


function Set-AppUserModelId([string]$shortcutPath, [string]$appUserModelId) {
    # WScript.Shell cannot write System.AppUserModel.ID. Use the Windows
    # property-store API so a pinned shortcut and the running EXE share one
    # taskbar identity instead of producing two ClipForge icons.
    $source = @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class ClipForgeShortcutIdentity {
  [ComImport, Guid("00021401-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl); void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cch); void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cch); void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cch); void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out ushort pwHotkey); void SetHotkey(ushort wHotkey); void GetShowCmd(out int piShowCmd); void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cch, out int piIcon); void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved); void Resolve(IntPtr hwnd, uint fFlags); void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
  }
  [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), ClassInterface(ClassInterfaceType.None)]
  class CShellLink {}
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    void GetCount(out uint cProps); void GetAt(uint iProp, out PROPERTYKEY pkey); void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv); void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv); void Commit();
  }
  [StructLayout(LayoutKind.Sequential, Pack=4)] struct PROPERTYKEY { public Guid fmtid; public uint pid; public PROPERTYKEY(Guid f, uint p){fmtid=f;pid=p;} }
  [StructLayout(LayoutKind.Explicit)] struct PROPVARIANT { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr ptr; }
  [DllImport("shell32.dll", CharSet=CharSet.Unicode, PreserveSig=false)] static extern void SHGetPropertyStoreFromParsingName(string pszPath, IntPtr pbc, uint flags, ref Guid riid, out IPropertyStore ppv);
  const ushort VT_LPWSTR=31; static readonly Guid IID_IPropertyStore=new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
  static readonly Guid PKEY_AppUserModel_ID=new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
  public static void SetId(string path,string id){ var store=(IPropertyStore)null; Guid iid=IID_IPropertyStore; SHGetPropertyStoreFromParsingName(path,IntPtr.Zero,2,ref iid,out store); var key=new PROPERTYKEY(PKEY_AppUserModel_ID,5); var pv=new PROPVARIANT(); pv.vt=VT_LPWSTR; pv.ptr=Marshal.StringToCoTaskMemUni(id); try{store.SetValue(ref key,ref pv);store.Commit();}finally{Marshal.FreeCoTaskMem(pv.ptr);Marshal.ReleaseComObject(store);} }
}
"@
    if (-not ('ClipForgeShortcutIdentity' -as [type])) { Add-Type -TypeDefinition $source -ErrorAction Stop | Out-Null }
    [ClipForgeShortcutIdentity]::SetId($shortcutPath, $appUserModelId)
}

$desktopShortcut = Join-Path $desktop 'ClipForge.lnk'
$startShortcut = Join-Path $startMenu 'ClipForge.lnk'
New-ClipForgeShortcut $desktopShortcut
New-ClipForgeShortcut $startShortcut
Set-AppUserModelId $desktopShortcut 'ClipForge.MediaDownloader'
Set-AppUserModelId $startShortcut 'ClipForge.MediaDownloader' 

# Remove the old script launcher if a previous repair created it.
$oldLaunchers = @((Join-Path $desktop 'ClipForge.pyw.lnk'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\ClipForge.lnk'))
foreach ($oldLauncher in $oldLaunchers) { if (Test-Path -LiteralPath $oldLauncher) { Remove-Item -LiteralPath $oldLauncher -Force -ErrorAction SilentlyContinue } }
Write-Output "Desktop shortcut: $desktopShortcut"
Write-Output "Start Menu shortcut: $startShortcut"
