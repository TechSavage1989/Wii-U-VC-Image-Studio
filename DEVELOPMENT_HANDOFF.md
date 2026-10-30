# Development handoff

## Baseline and work in progress

Recovered user-confirmed Image Builder v5.05, SHA-256
72dc7e76aa5b22d87cdcc07d10416eb4be6deac7ea5417a24ef7fcb6f38590ed.
Repository initially contained only a README at
8babdafa5b54fcc3ab5af2bc7023e1bb0059fda8. Public source and releases approved
2026-10-30. Studio 5.06.0 source is implemented; native validation and release
publication are pending. No tag or release exists yet. Do not call it released.

Read AGENTS.md for scope and folder responsibilities. Source package remains
vc_extractor/ with bundled webui_assets/; no legacy function has been removed.
No application-wide license was present in the recovered baseline. Preserve
THIRD_PARTY_NOTICES.txt; public visibility is not a license grant.

## Persistent data

Preserve %APPDATA%/Wii_U_VC_Image_Builder and its config_location.json pointer.
Existing custom configuration paths and generated output locations are retained.
Updater data will live in %LOCALAPPDATA%/Wii_U_VC_Image_Studio/updates.

## Validation and publication

Local validation: 190 tests passed, followed by 47 targeted tests including two
additional native-health/busy-install guards (192 total now collected). JS syntax,
CSS parse and root layout pass. Real Chromium production-length timers are running.
Windows native startup and packaged helper/health/rollback tests are defined in CI,
not yet verified. First release has no prior hosted Studio version to upgrade from.

## Commands and source map

Install requirements.txt plus requirements-dev.txt using Python 3.12. Run
python -m pytest -q; python scripts/check_root.py; python scripts/browser_smoke.py
--timers; on Windows run python scripts/windows_native_smoke.py and
python scripts/build_windows.py, then python scripts/windows_update_smoke.py.
python scripts/package_source.py produces a single-folder source ZIP without Git.
CI extracts that source and builds it on Windows, and tests it on Linux.

The natural Python package remains vc_extractor/ rather than forcing a src/ move.
Original resources remain assets/. The WebUI stays colocated in webui_assets/.
Old README history moved to docs/releases/baseline-v5.05/README.md; WEBUI_NOTES.md
moved to docs/architecture.md; shortcut helper moved to scripts/. Build logic is
scripts/build_windows.py; root BAT files are small entry wrappers. Merge old
branches into these locations; do not recreate old root release notes or outputs.
scripts/root_allowlist.json is enforced for tracked files and extracted archives.

## Update contract and limits

Stable filename: Wii_U_VC_Image_Studio.exe. Preserve renamed running EXEs in-place.
Feed: https://github.com/TechSavage1989/Wii-U-VC-Image-Studio/releases/latest/download/update-manifest.json.
Repository/feed are public, no token embedded. Publish unique vVERSION tags only
after main CI. Tag CI verifies version/digests and refuses existing release names.
No application-wide license chosen. Builds are unsigned. Keep upstream notices.
v5.05 requires manual migration and explicit shortcut repointing. Restart closes
the unsaved ROM list; settings and exports persist. Failed staging is retained.
One rollback copy per installation path; do not clean user configuration or ROMs.

README follows the requested centered authentic icon/H1/navigation and user-first
section order. No screenshots, unverified badges or invented license. Release
links currently point to the index because no release is published.
