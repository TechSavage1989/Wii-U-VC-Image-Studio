# Validation

Local validation: 194 tests pass. JavaScript syntax, CSS parsing, root allowlist
and README headings, navigation, local links and icon path pass. The actual README
was rendered and visually inspected. Hosted Windows source startup, bridge health,
viewport fit and a build from a fresh source archive without Git have passed.
Packaged cleanup/rollback and final browser timer results remain pending.

Native negative control: run 41002656006 demonstrated a cleanup failure while the
one-file helper bootloader still held its EXE. Cleanup now waits for both helper
processes and retries transient Windows locks within the same owned session.
An inherited path test also required native-separator-independent assertions.

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
desktop interaction, or historical published Studio-to-Studio upgrade is claimed
without explicit evidence. The first Studio release has no earlier Studio release.
