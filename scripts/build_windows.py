"""Build Windows x64 one-file executable and immutable release assets."""
from pathlib import Path
import json
import os
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vc_extractor.help_docs import APP_VERSION
from vc_extractor.updater import APP_ID, EXE, RELEASES, digest, validate_pe, validate_manifest, version_key


def main():
    if os.name != "nt":
        raise SystemExit("Build on Windows x64 with Python 3.12 x64")
    subprocess.run([sys.executable, "scripts/check_root.py"], cwd=ROOT, check=True)
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    version = version_key(APP_VERSION) + (0,)
    resource = ROOT / "build" / "version.txt"
    resource.parent.mkdir(exist_ok=True)
    fields = {"CompanyName": "TechSavage1989", "FileDescription": "Wii U VC Image Studio", "FileVersion": APP_VERSION,
              "ProductName": "Wii U VC Image Studio", "ProductVersion": APP_VERSION, "OriginalFilename": EXE,
              "InternalName": "Wii_U_VC_Image_Studio"}
    strings = ",".join(f"StringStruct({key!r}, {value!r})" for key, value in fields.items())
    resource.write_text(f"VSVersionInfo(ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', [{strings}])]), VarFileInfo([VarStruct('Translation', [1033,1200])])])", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onefile",
                    "--name", Path(EXE).stem, "--version-file", str(resource), "--icon", str(ROOT / "assets/vc_title_extractor.ico"),
                    "--collect-all", "webview", "--hidden-import", "webview.platforms.edgechromium",
                    "--hidden-import", "miniaudio", "--hidden-import", "_miniaudio", "--hidden-import", "_cffi_backend",
                    "--add-data", str(ROOT / "assets") + ";assets",
                    "--add-data", str(ROOT / "vc_extractor/webui_assets") + ";vc_extractor/webui_assets",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"),
                    "--specpath", str(ROOT / "build"), str(ROOT / "app.py")], cwd=ROOT, check=True)
    exe = ROOT / "dist" / EXE
    validate_pe(exe, APP_VERSION)
    identity = {"app_id": APP_ID, "version": APP_VERSION, "exe_sha256": digest(exe)}
    archive = out / f"Wii_U_VC_Image_Studio_v{APP_VERSION}_Windows_x64_Portable.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        prefix = "Wii_U_VC_Image_Studio/"
        bundle.write(exe, prefix + EXE)
        bundle.writestr(prefix + "release.json", json.dumps(identity, indent=2))
        bundle.write(ROOT / "docs/portable-readme.txt", prefix + "README.txt")
        bundle.write(ROOT / "THIRD_PARTY_NOTICES.txt", prefix + "THIRD_PARTY_NOTICES.txt")
    manifest = {"schema": 1, **identity, "platform": "windows-x64", "size": archive.stat().st_size,
                "sha256": digest(archive), "url": RELEASES + f"/download/v{APP_VERSION}/{archive.name}",
                "notes": ["Preserves the v5.05 artwork, capture, preview and sound workflows under the Studio name.",
                          "Adds explicit, verified portable updates with stable executable naming and rollback.",
                          "Keeps existing settings and custom configuration-folder choices."]}
    validate_manifest(manifest)
    (out / "update-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    subprocess.run([sys.executable, "scripts/package_source.py"], cwd=ROOT, check=True)
    files = sorted(p for p in out.iterdir() if p.suffix in {".zip", ".json"})
    (out / "SHA256SUMS.txt").write_text("".join(f"{digest(p)}  {p.name}\n" for p in files), encoding="utf-8")


if __name__ == "__main__":
    main()
