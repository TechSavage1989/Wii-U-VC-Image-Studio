# Updates and recovery

The canonical repository is public source plus public releases. The stable feed is
https://github.com/TechSavage1989/Wii-U-VC-Image-Studio/releases/latest/download/update-manifest.json.
Prereleases are available on the releases index and do not enter the stable feed.
No embedded token or account setup is required. No API polling is used for normal
checks: GitHub's release asset endpoint supports HTTP conditional requests.

Checks use one in-flight request, 30-second manual cooldown, 2-minute focus
cooldown, 5-minute periodic checks, exponential error backoff and Retry-After.
Cached metadata is not presented as a successful offline check. No check starts
a download. Cached error backoff is persisted where possible.

Download verifies an allowlisted repository URL, HTTPS redirects, maximum size,
SHA-256 and an exact ZIP file allowlist. The package has four files under one root.
Symlinks, duplicate entries, traversal, extra executables and excessive expansion
are rejected. The internal release.json and Windows PE product/version resources
must match the manifest. Hashes are not code signatures and cannot protect against
compromise of the authorized publisher account/feed itself.

Installation uses a copied helper executable under per-user update staging. It
waits for the old process to exit, copies the payload onto the destination volume,
replaces the current filename atomically, retains one previous executable and
launches the new app with a one-time health token. The new native window must be
shown and its JavaScript bridge/rendering ready before it acknowledges health.
Missing health causes rollback. PyInstaller child processes get a fresh unpacking
environment. Successful staging cleanup waits for the helper process to exit.

## Manual migration and recovery

v5.05 cannot self-update. Extract Studio separately and launch it manually. Its
settings loader intentionally keeps the old Image Builder directory and custom
configuration pointer. Repoint legacy shortcuts yourself; Studio does not silently
rewrite user shortcuts or rename an existing custom executable.

If automatic recovery cannot complete, close all Studio windows. Review
last-result.json/helper-error.json under the per-user updates directory. The
rollback subfolder contains one previous.exe per full application path. Copy the
appropriate one back to that installation's original executable name only after
confirming the path. Do not remove settings or the configured export directory.
Failed staging is diagnostic evidence and is not automatically deleted.

The in-memory ROM list is not session-persisted by this release. The install
confirmation explicitly warns that it closes; generated files and settings remain.
