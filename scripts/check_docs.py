"""Validate local README destinations and render the actual Markdown for review."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def check():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, re.M)
    assert headings == ["Features", "Download", "Requirements", "Getting started", "How to use", "Updates and settings", "Troubleshooting", "Development"], headings
    assert text.count("<h1 ") == 1 and not re.search(r"^# ", text, re.M)
    anchors = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in headings}
    destinations = re.findall(r"\]\(([^)]+)\)|(?:href|src)=\"([^\"]+)\"", text)
    for pair in destinations:
        link = pair[0] or pair[1]
        if link.startswith("#"):
            assert link[1:] in anchors, link
        elif not link.startswith("https://"):
            assert (ROOT / link.split("#")[0]).is_file(), link
    print("README heading order, navigation, local links and authentic icon path passed")
    return text


if __name__ == "__main__":
    text = check()
    if "--render" in sys.argv:
        import markdown
        out = ROOT / "artifacts/readme-preview.html"
        out.parent.mkdir(exist_ok=True)
        html = markdown.markdown(text, extensions=["fenced_code", "tables"])
        html = html.replace('src="assets/', 'src="../assets/')
        out.write_text('<!doctype html><meta charset="utf-8"><style>body{max-width:950px;margin:32px auto;padding:20px;font:16px/1.55 Arial;color:#202630}h2{border-bottom:1px solid #ddd;padding-bottom:8px}pre{padding:16px;background:#f1f3f5;overflow:auto}a{color:#0969da}</style>' + html, encoding="utf-8")
        print(out)
