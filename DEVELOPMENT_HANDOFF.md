# Development handoff

## Current baseline

Recovered user-confirmed Image Builder v5.05, SHA-256
72dc7e76aa5b22d87cdcc07d10416eb4be6deac7ea5417a24ef7fcb6f38590ed.
Repository initially contained only a README at
8babdafa5b54fcc3ab5af2bc7023e1bb0059fda8. Public source and releases approved
2026-10-30. Studio 5.06.0 is the repository-maintained baseline. Release identity
is v5.06.0. Confirm the tag, successful release workflow and downloadable assets
before treating a CI artifact as a published release. The release workflow runs
the Windows and browser gates again and verifies the public feed after publishing.

Read AGENTS.md for scope and folder responsibilities. Source package remains
vc_extractor/ with bundled webui_assets/; no legacy function has been removed.
No application-wide license was present in the recovered baseline. Preserve
THIRD_PARTY_NOTICES.txt; public visibility is not a license grant.

## Persistent data

Preserve %APPDATA%/Wii_U_VC_Image_Builder and its config_location.json pointer.
Existing custom configuration paths and generated output locations are retained.
Updater data will live in %LOCALAPPDATA%/Wii_U_VC_Image_Studio/updates.

## Validation and publication

194 tests pass locally, from a fresh no-Git source archive, and in hosted Windows
CI. JS syntax, CSS parse, root layout and README heading/navigation/local-link
checks pass. The actual README was rendered and inspected. Hosted Windows source
startup, clean-source packaging and packaged cross-drive install/health/cleanup
and failed-health rollback pass. Real Chromium two/five-minute timers and actual
focus-return visibility pass. Evidence: Actions run 41003479695, source
20d51eebfa33b564f3a99ba2a268104a283f9daa. The final release ref also reruns these gates.

Windows validation uses the hosted Windows Server 2025 x64 runner, not manual
Windows 10/11 desktop testing. No complete emulator capture matrix was retested.
This first release cannot exercise a historical Studio-to-Studio hosted upgrade.
The packaged fixture uses a marked copy of the new EXE as its old executable;
the post-publication HTTPS check validates download/staging but does not install.

## Commands and source map

Install requirements.txt plus requirements-dev.txt using Python 3.12. Run
python -m pytest -q; python scripts/check_root.py; python scripts/browser_smoke.py
--timers; on Windows run python scripts/windows_native_smoke.py and
python scripts/build_windows.py, then python scripts/windows_update_smoke.py.
python scripts/package_source.py produces a single-folder source ZIP without Git.
After publication, python scripts/verify_hosted_release.py checks the real HTTPS
feed, explicit download, archive/digests and PE structure, without installing.
CI extracts that source and builds it on Windows, and tests it on Linux.
python scripts/check_docs.py verifies README structure and local links. Optional
--render requires the Markdown package and writes a local review HTML file.
The browser timing test uses Chromium without Playwright's forced-focus emulation.
It waits real intervals and switches actual tab visibility, without fake clocks.
The local sandbox blocks raw Chromium sockets; hosted CI runs that check instead.

The natural Python package remains vc_extractor/ rather than forcing a src/ move.
Original resources remain assets/. The WebUI stays colocated in webui_assets/.
Old README history moved to docs/releases/baseline-v5.05/README.md; WEBUI_NOTES.md
moved to docs/architecture.md; shortcut helper moved to scripts/. Build logic is
scripts/build_windows.py; root BAT files are small entry wrappers. Merge old
branches into these locations; do not recreate old root release notes or outputs.
scripts/root_allowlist.json is enforced for tracked files and extracted archives.
The archived v5.05 README is a deliberate historical-format exception, not the
current launch/build guide. Current architecture notes use the new build paths.

## Update contract and limits

Stable filename: Wii_U_VC_Image_Studio.exe. Preserve renamed running EXEs in-place.
Feed: https://github.com/TechSavage1989/Wii-U-VC-Image-Studio/releases/latest/download/update-manifest.json.
Repository/feed are public, no token embedded. Publish unique vVERSION tags only
after main CI. Pushing a source/workflow commit to release/VERSION is the explicit
publication trigger. API branch creation alone may not emit the required push.
Release CI verifies ref/version/digests and refuses existing release names.
No application-wide license chosen. Builds are unsigned. Keep upstream notices.
v5.05 requires manual migration and explicit shortcut repointing. Restart closes
the unsaved ROM list; settings and exports persist. Failed staging is retained.
One rollback copy per installation path; do not clean user configuration or ROMs.

README follows the requested centered authentic icon/H1/navigation and user-first
section order. No screenshots, unverified badges or invented license. The release
index is always linked; main's download section adds the stable latest-release
shortcut only after its destination is verified. Historical source snapshots may
use the releases index alone. GitHub's pixel-rendered page was not inspected;
local Markdown rendering and repository paths were inspected.
