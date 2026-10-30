# Read first

Read this file and DEVELOPMENT_HANDOFF.md before changes. Fetch current main,
branches, open PRs and releases; do not replace newer source with old archives.

## Approved scope

- Owner: TechSavage1989. Public source and Windows portable releases in
  https://github.com/TechSavage1989/Wii-U-VC-Image-Studio are explicitly approved.
- Preserve v5.05 artwork, capture, sound, icon, responsive layout and settings.
- No credentials, ROMs, downloaded community content or MyInstants audio in Git.
- Never change visibility, invent a license, overwrite releases or force-push.
- No servers, Codex automation, unattended workers, payments or account setup.

## Source map and root policy

- vc_extractor/: Python package and colocated bundled WebUI. Keep this natural
  package layout so source and PyInstaller resource paths agree.
- assets/: authentic icon, existing templates and two original startup sounds.
- tests/: regression and updater tests; scripts/: build and validation tools.
- docs/: architecture and upgrade guidance; docs/releases/: release history.
- Root exceptions: app.py/app.pyw are desktop entrypoints; Run.bat,
  Setup_and_Run.bat and Build_Windows_EXE.bat are small Windows entry wrappers.
- scripts/root_allowlist.json is reviewed policy, checked in CI and archives.
  Required new root entries need a documented reason here and in the handoff.
- Do not move paths beneath concurrent work. Update resource/build/test/link
  references together and test a clean source archive without .git.

## Release rules

Use the stable Wii_U_VC_Image_Studio.exe launch name. Preserve custom existing
executable filenames on in-app update. Keep legacy settings paths intact.
Use per-user LocalAppData for updater cache, staging, log and one rollback copy.
Checks never download or restart. Install must be explicit and refuse active
operations. Require allowlisted HTTPS, version/identity, SHA-256, archive and PE
validation. A digest is not publisher signing. Confirm renderer plus native
window health before success and retain rollback on failure.

Use unique versions/tags. Publish only after tests and Windows CI pass. Report
native Windows and old-to-new update checks that were not performed. Do not call
untested native behavior verified. Documentation-only work does not bump versions.
Keep README user-first and the current handoff concise; archive history in docs.

Normal main-branch CI does not publish. A version-matched release/VERSION branch
can run the same gates and publish vVERSION once, using the workflow's scoped
GitHub token. Creating that branch is a publication action and requires approved
release scope. Documentation-only main pushes skip binary builds and publication.
