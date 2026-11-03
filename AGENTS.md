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

Normal main-branch CI does not publish. Pushing a source/workflow commit to a
version-matched release/VERSION branch runs the same gates and can publish
vVERSION once, using the workflow's scoped GitHub token. Branch creation through
the API alone does not reliably emit a push. Updating that release ref is a
publication action and requires approved release scope. Documentation-only main
pushes skip binary builds and publication.

## Repository standards (all contributors and AI tools)

This repository follows the shared TechSavage1989 standard in [`docs/REPOSITORY_STANDARDS.md`](docs/REPOSITORY_STANDARDS.md), the same file in every app repository. Read it before changing anything. In short:

- **README:** centered header (real icon, one `<h1>`, tagline, quick links Download · Get started · Features · Screenshots · Updates · Development, hero image), then the standard section order. Update it in the same pull request as any user-visible change.
- **Screenshots:** real captures with fictional data in `docs/images/` (`hero.png` + kebab-case names). Recapture any screenshot a UI change makes stale, in the same pull request.
- **Layout:** compact root; respect the root allowlist and checks above. Never commit build output, binaries, archives, logs, caches or secrets.
- **Branches:** short-lived `feat/` `fix/` `docs/` `chore/` `release/` branches, one focused change per pull request, merge only on green checks, delete the branch after merging.
- **Instructions:** this file is the single source of project rules for every tool; `CLAUDE.md` imports it. Add new rules and gotchas here.

Where this file's project-specific rules differ from the shared standard, this file wins; an enforced check script wins over both.
