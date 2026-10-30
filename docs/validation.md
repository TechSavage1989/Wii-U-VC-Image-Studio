# Validation

Validation is in progress. Do not treat a written test or a configured workflow
as evidence that it passed. Hosted Windows build, native GUI and real release
download checks must be recorded in DEVELOPMENT_HANDOFF.md before handoff.

The unit suite preserves the v5.05 regressions and adds update URL, manifest,
single-flight, ETag, cooldown, backoff, offline, ZIP, PE and cleanup checks.
Negative controls corrupt a download digest, add unsafe archive entries and add
an unapproved root file in isolated fixtures. Browser tests exercise the actual
JavaScript scheduler and page layout. Native checks must use Windows WebView2.

No publisher signing, full emulator capture across every console, manual Windows
desktop interaction, or historical published Studio-to-Studio upgrade is claimed
without explicit evidence. The first Studio release has no earlier Studio release.
