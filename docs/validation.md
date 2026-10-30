# Validation

Stable v5.06.0 is published from ac498fb0569900c5c7bd71a668addbbd94365e90.
[Release run 41004430697](https://github.com/TechSavage1989/Wii-U-VC-Image-Studio/actions/runs/41004430697)
passed all three jobs, including actual public feed retrieval, portable download,
SHA-256, archive identity and staging. The tag resolves to that exact commit.
Four hosted assets and their digests were verified against the publication logs.

194 tests pass locally, in a fresh no-Git source archive, and on hosted Windows.
JavaScript syntax, CSS parsing, root allowlist and README headings, navigation,
local links and icon path pass. The actual README was rendered and visually
inspected. Hosted Windows source startup, bridge health, viewport fit and a build
from a fresh source archive without Git pass. Packaged cross-drive install,
visible health, cleanup after helper exit, settings preservation, custom filename,
one backup and failed-health rollback pass. Real browser focus return and the
production-length two/five-minute intervals pass.

Prepublication evidence: [Actions run 41003479695](https://github.com/TechSavage1989/Wii-U-VC-Image-Studio/actions/runs/41003479695),
source 20d51eebfa33b564f3a99ba2a268104a283f9daa. The release pipeline reruns these
gates for its exact ref, then uses the updater to verify the actual public HTTPS
feed, ZIP download, digests and staging. This download check does not install.
Windows CI uses Windows Server 2025 x64. Manual Windows 10/11 testing was not done.

Native negative control: run 41002656006 demonstrated a cleanup failure while the
one-file helper bootloader still held its EXE. Cleanup now waits for both helper
processes and retries transient Windows locks within the same owned session.
An inherited path test also required native-separator-independent assertions.
A local browser negative control removed the offline-render guard and reproduced
a stale success message while offline. The released guard retained the offline
error through a cached-success repaint; the browser regression also checks this.

The unit suite preserves the v5.05 regressions and adds update URL, manifest,
single-flight, ETag, cooldown, backoff, offline, ZIP, PE and cleanup checks.
Negative controls corrupt a download digest, add unsafe archive entries and add
an unapproved root file in isolated fixtures. Browser tests exercise the actual
JavaScript scheduler and page layout. Playwright's forced focus is deliberately
not used for timing acceptance: a separate Chromium process exercises real hidden
and visible tab transitions and actual two/five-minute intervals. The local
sandbox blocks that process's socket; it runs in hosted CI. Native checks use
Windows WebView2. Windows update tests use different staging/install drives where
available, a custom EXE filename, settings preservation and one backup.

The packaged transition fixture marks an otherwise current EXE as the old file;
this is not a historical version-to-version upgrade. Its failed-health fixture
blocks configuration storage for both launches, so it verifies restored bytes,
not a usable rollback window under that deliberately broken environment.

No publisher signing, full emulator capture across every console, manual Windows
desktop interaction, or historical published Studio-to-Studio upgrade is claimed.
The first Studio release has no earlier Studio release. Local Markdown rendering
was inspected; the hosted GitHub page was not pixel-rendered for inspection.
