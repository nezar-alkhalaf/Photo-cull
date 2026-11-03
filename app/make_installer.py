"""Build PhotoCull-Setup.exe: one file a friend double-clicks to install PhotoCull.

Run:  app\.venv\Scripts\python app\make_installer.py
It packs the files of the last commit (nothing private: no photos, styles, settings or
.venv) plus app/installer/install.bat into a Windows self-extracting installer made with
IExpress, which ships with Windows. The result lands on the Desktop.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_BAT = Path(__file__).resolve().parent / "installer" / "install.bat"

SED = """[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName={target}
FriendlyName=PhotoCull
AppLaunched=cmd /c install.bat
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="install.bat"
FILE1="payload.zip"
[SourceFiles]
SourceFiles0={folder}\
[SourceFiles0]
%FILE0%=
%FILE1%=
"""


def version() -> str:
    text = (ROOT / "app" / "photocull" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def main() -> int:
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("WARNING: uncommitted changes are NOT in the installer (commit first):\n" + dirty)
    build = Path(tempfile.mkdtemp(prefix="pc_build_"))          # plain ASCII path for IExpress
    subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", "-o",
                    str(build / "payload.zip"), "HEAD"], check=True)
    shutil.copy(INSTALL_BAT, build / "install.bat")
    exe = build / "PhotoCull-Setup.exe"
    sed = build / "photocull.sed"
    sed.write_text(SED.format(target=exe, folder=build), encoding="ascii")
    subprocess.run(["iexpress", "/N", "/Q", str(sed)], check=True)
    if not exe.is_file():
        print("IExpress did not produce the installer.")
        return 1
    out = Path.home() / "Desktop" / f"PhotoCull-Setup-{version()}.exe"
    shutil.copy(exe, out)
    print(f"Done: {out}  ({out.stat().st_size // 2**20} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
