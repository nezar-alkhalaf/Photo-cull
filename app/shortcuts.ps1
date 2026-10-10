# Creates the PhotoCull shortcut (with the camera icon) in the project folder and on the Desktop.
# Run by setup.bat; safe to run again. A shortcut stores the full path, so after moving the
# folder to another PC run First-Time Setup again (it re-creates the shortcut).
# Uses the Windows IShellLinkW interface directly: WScript.Shell cannot handle Arabic paths.
param([switch]$NoDesktop)

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class PhotoCullShortcut
{
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW
    {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f, int cch, IntPtr pfd, int flags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder n, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string n);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder d, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string d);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder a, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string a);
        void GetHotkey(out short k);
        void SetHotkey(short k);
        void GetShowCmd(out int c);
        void SetShowCmd(int c);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder p, int cch, out int i);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string p, int i);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string p, int r);
        void Resolve(IntPtr hwnd, int flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string f);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink { }

    public static void Create(string lnk, string target, string workDir, string icon, string desc)
    {
        IShellLinkW l = (IShellLinkW)new ShellLink();
        l.SetPath(target);
        l.SetWorkingDirectory(workDir);
        l.SetIconLocation(icon, 0);
        l.SetDescription(desc);
        l.SetShowCmd(7);                       // minimised: no console flash
        ((IPersistFile)l).Save(lnk, true);
    }
}
'@

$app = $PSScriptRoot
$root = Split-Path -Parent $app
$target = Join-Path $root 'Start PhotoCull.bat'
$icon = Join-Path $app 'assets\photocull.ico'

[PhotoCullShortcut]::Create((Join-Path $root 'PhotoCull.lnk'), $target, $root, $icon, 'PhotoCull')
if (-not $NoDesktop) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    [PhotoCullShortcut]::Create((Join-Path $desktop 'PhotoCull.lnk'), $target, $root, $icon, 'PhotoCull')
}
