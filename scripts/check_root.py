"""Tracked-root guard with the same explicit policy in source archives."""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {".github", "vc_extractor", "assets", "tests", "scripts", "docs"}
GENERATED = {".git", ".venv", ".buildvenv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", "artifacts", "test-results", "node_modules"}


def source_files(root):
    root = Path(root)
    if (root / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
        files = [Path(x) for x in result.stdout.decode().split("\0") if x]
        result = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root, capture_output=True, check=True)
        files += [Path(x) for x in result.stdout.decode().split("\0") if x]
        return sorted(set(files))
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file()
                  and not any(part in GENERATED for part in p.relative_to(root).parts)
                  and p.suffix not in {".pyc", ".pyo"})


def check(root=ROOT):
    root = Path(root)
    allow = set(json.loads((root / "scripts/root_allowlist.json").read_text()))
    errors = []
    files = source_files(root)
    for path in files:
        if (len(path.parts) == 1 and str(path) not in allow) or (len(path.parts) > 1 and path.parts[0] not in DIRECTORIES):
            errors.append(str(path))
        if any(part in GENERATED for part in path.parts) or path.suffix.lower() in {".exe", ".zip", ".pyc", ".pyo", ".mp3"}:
            errors.append(str(path))
    if errors:
        raise ValueError("Unapproved source paths: " + ", ".join(sorted(set(errors))))
    return files


if __name__ == "__main__":
    try:
        print(f"Source layout OK: {len(check(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT))} files")
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
