from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_root import check
from vc_extractor.help_docs import APP_VERSION

if __name__ == "__main__":
    files = check(ROOT)
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    target = output / f"Wii_U_VC_Image_Studio_v{APP_VERSION}_Source.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(ROOT / path, str(Path("Wii_U_VC_Image_Studio_Source") / path))
    print(target)
