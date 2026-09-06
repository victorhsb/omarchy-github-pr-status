# Validation

Validated on 2026-09-06 with Omarchy 4.0.2, Qt 6.11.2, Python 3.14, and GitHub CLI 2.100.0.

## Automated checks

- 22 Python tests cover scope, drafts, deduplication, pagination, check mappings,
  review decisions, separate comment totals, unavailable details, rate limits,
  account changes, cache permissions, and three concurrent monitor refreshes.
- Seven Qt Quick test scenarios (plus setup/cleanup) exercise keyboard dispatch,
  title clicks, expansion/collapse, selection after refresh, initial scroll
  positioning, empty/error states, and dark/light preview rendering.
- Omarchy manifest validation and QML lint against installed shell imports pass.
  Lint suppresses dynamic QObject member warnings and the known missing
  `QProcess::ExitStatus` metadata in Quickshell's exported QML types.

## Live verification

- Installed in the user-owned plugin directory and loaded by the existing shell.
- Verified disable/re-enable and shell summon/hide; the plugin remains enabled.
- Authenticated through the existing `gh` account; fetched nine authored open PRs
  including drafts, without API errors or incomplete results.
- A live PR had 123 checks, exercising a second check page against GitHub.
- Visually checked the real panel, found and fixed the initial first-row clipping,
  and added a regression test for variable-height rows.
- The desktop has two active displays; the bar widget loads in the existing
  per-display bars. Shared-fetch coordination is also tested with actual processes.
- Dark/light previews use fictional PRs and isolated theme tokens. The live panel
  uses the user's active Omarchy theme. No private PR data is included in previews.

Browser opening is tested to the PR URL signal boundary; opening a real PR uses
Qt's standard external-URL handler. Multi-account/Enterprise support, notifications,
and GitHub write actions are intentionally outside this version.
