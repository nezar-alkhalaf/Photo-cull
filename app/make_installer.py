r"""Build PhotoCull-Setup.exe: one file a friend double-clicks to install PhotoCull.

Run:  app\.venv\Scripts\python app\make_installer.py

It packs the files of the last commit (nothing private: no photos, styles, settings or
.venv) and app/installer/install.bat into a tiny launcher program compiled with the C#
compiler that ships with Windows (no extra tools). The launcher unpacks them to a temporary
folder, runs install.bat in a visible window and cleans up. The result lands on the Desktop
with the PhotoCull camera icon.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = Path(__file__).resolve().parent
INSTALL_BAT = APP / "installer" / "install.bat"
ICON = APP / "assets" / "photocull.ico"

LAUNCHER = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

static class Program
{
    static int Main()
    {
        string tmp = Path.Combine(Path.GetTempPath(), "PhotoCull-setup-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        Directory.CreateDirectory(tmp);
        try
        {
            foreach (string name in new[] { "payload.zip", "install.bat" })
            {
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
                using (FileStream f = File.Create(Path.Combine(tmp, name)))
                    s.CopyTo(f);
            }
            var psi = new ProcessStartInfo("cmd.exe", "/c \"\"" + Path.Combine(tmp, "install.bat") + "\"\"");
            psi.WorkingDirectory = tmp;
            psi.UseShellExecute = false;
            using (Process p = Process.Start(psi))
            {
                p.WaitForExit();
                return p.ExitCode;
            }
        }
        finally
        {
            try { Directory.Delete(tmp, true); } catch { }
        }
    }
}
"""


def version() -> str:
    text = (APP / "photocull" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def find_csc() -> Path:
    for base in (r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319",
                 r"C:\Windows\Microsoft.NET\Framework\v4.0.30319"):
        csc = Path(base) / "csc.exe"
        if csc.is_file():
            return csc
    raise SystemExit("The C# compiler (csc.exe) was not found on this PC.")


def main() -> int:
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("WARNING: uncommitted changes are NOT in the installer (commit first):\n" + dirty)
    build = Path(tempfile.mkdtemp(prefix="pc_build_"))          # plain ASCII path for csc
    subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", "-o",
                    str(build / "payload.zip"), "HEAD"], check=True)
    shutil.copy(INSTALL_BAT, build / "install.bat")
    (build / "launcher.cs").write_text(LAUNCHER, encoding="utf-8")
    exe = build / "PhotoCull-Setup.exe"
    subprocess.run([str(find_csc()), "/nologo", "/target:exe", f"/out:{exe}",
                    f"/win32icon:{ICON}",
                    f"/resource:{build / 'payload.zip'},payload.zip",
                    f"/resource:{build / 'install.bat'},install.bat",
                    str(build / "launcher.cs")], check=True)
    out = Path.home() / "Desktop" / f"PhotoCull-Setup-{version()}.exe"
    shutil.copy(exe, out)
    print(f"Done: {out}  ({out.stat().st_size / 2**20:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
