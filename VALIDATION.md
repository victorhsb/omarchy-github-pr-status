# Validation

Resource-limit fix validated on 2026-09-07 with Omarchy 4.0.2, Qt 6.11.2,
and Python 3.14.7. GitHub responses in this verification use fictional fixtures;
the earlier live verification is recorded separately below.

## Resource-limit security fix

- Implemented a shared 60-second monotonic deadline covering lock waiting,
  account resolution, discovery, detail batches, and nested pagination. Requests
  receive at most 35 seconds or the remaining refresh budget.
- Bounded discovery to 100 PRs / ten pages, and checks/reviews to five pages and
  500 entries each per PR, counting initial pages, duplicates, and pending reviews.
- Bounded subprocess stdout/stderr to 8 MiB / 64 KiB and retained strings to
  their documented limits. Real local child processes verify overflow and timeout
  termination/reaping, concurrent pipe draining, and blocked-stdin handling.
- Bounded cache reads before JSON parsing and validated cached field shapes,
  string/collection sizes, timestamps, and check totals before reuse. Atomic writes
  and 700/600 permissions remain covered.
- Incremental JSON encoding enforces a 2 MiB budget including escaping, metadata,
  and the trailing newline. Oversized snapshots omit oldest rows, mark the result
  incomplete/stale, and preserve the previous last-complete-update timestamp.
- An end-to-end helper test uses a local fictional `gh` executable: four PRs with
  100 long Unicode check names each produce three retained PRs under the byte
  limit. Both a fresh fetch and a cache hit emit valid bounded JSON without the
  internal authentication fingerprint.
- `python3 -m unittest discover -s tests -v`: all 47 tests passed, including the
  original 22 regressions. Fake-clock tests verify no further requests after
  deadline expiry during discovery, batches, or nested check/review pagination.
- `omarchy plugin validate .` and `python3 tests/check_qml.py`: passed.
- `python3 tests/check_ui.py`: all eight scenarios passed (ten results including
  setup/cleanup). The new capped-result scenario checks incomplete/stale labels,
  previous timestamps, unknown checks, and unavailable inline counts.
- Regenerated and visually inspected both fictional dark/light previews; text,
  cards, check bars, and footer remain readable and correctly positioned.

This verification did not install the checkout, run a live account fetch, or
publish a commit/comment. Hosted Python 3.10/3.14 CI remains separate from these
local results.

## Stable release preparation (2026-09-06)

- Public repository identity confirmed as `victorhsb/omarchy-github-pr-status`;
  permanent plugin ID remains `torugo.github-pr-status`.
- Manifest homepage and version 1.0.0, primary Git installation/update commands,
  dependency requirements, MIT attribution, and fictional previews reviewed.
- Re-ran all 22 Python tests, seven Qt Quick scenarios (nine results including
  setup/cleanup), QML lint, and manifest validation successfully.
- Replaced the local development installation with a fresh clone through
  `omarchy plugin add` from the public repository. The previous non-Git copy was
  backed up by Omarchy before removal.
- Verified Git-installed disable/re-enable, summon/hide, up-to-date update,
  removal, and fresh reinstall through Omarchy's standard commands.
- Added hosted CI on Python 3.10 and 3.14 with read-only repository permissions
  and pinned official action revisions. Hosted CI is separate from local QML
  and desktop validation; the release is gated on its successful run.

## Original release checks (2026-09-06)

- 22 Python tests cover scope, drafts, deduplication, pagination, check mappings,
  review decisions, separate comment totals, unavailable details, rate limits,
  account changes, cache permissions, and three concurrent monitor refreshes.
- Seven Qt Quick test scenarios (plus setup/cleanup) exercise keyboard dispatch,
  title clicks, expansion/collapse, selection after refresh, initial scroll
  positioning, empty/error states, and dark/light preview rendering.
- Omarchy manifest validation and QML lint against installed shell imports pass.
  Lint suppresses dynamic QObject member warnings and the known missing
  `QProcess::ExitStatus` metadata in Quickshell's exported QML types.

## Live verification (2026-09-06)

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
Qt's standard external-URL handler. Multi-account/Enterprise support and GitHub write actions are intentionally outside
this version.

## Ready-to-merge notifications (2026-09-09)

- All 53 Python tests pass, including review cycles, delayed merge readiness,
  restart persistence, account invalidation, failure recovery, blockers, markup
  escaping, and bounded best-effort notification delivery.
- Plugin validation, QML lint, and all 10 Qt Quick checks pass.
- Review-cycle state shares the private locked cache used across monitors.
- Backed up and installed the checkout; shell rescan succeeded and installed
  runtime files match the checkout. A live GitHub refresh returned complete, fresh
  data including mergeability fields. The desktop service accepted a setup
  notification. A real review-to-ready transition remains pending live acceptance.

## Stack grouping (2026-09-09)

- All 58 Python tests and 11 Qt Quick checks pass; plugin validation and QML lint pass.
- Regression coverage checks repository/stack grouping, bottom-to-top ordering,
  original positions with hidden members, old caches, invalid metadata, stale
  retention, removal from stacks, and keyboard selection across regrouping.
- Dark/light fictional previews were regenerated and visually inspected.
- Live GitHub schema and complete refresh verified; seven authored open PRs had
  stack membership. No private PR metadata is included in the previews or report.

## Visual stack subgroups and live reload correction (2026-09-09)

- Stack headings now sit outside the PR cards; stack members are indented and
  joined by a vertical guide. Standalone cards remain at the top level.
- QML lint and all 11 UI checks pass, including heading placement, indentation,
  group boundaries, and keyboard navigation. Both theme previews inspected.
- Installed files and cached membership were correct, but rescan alone did not
  make the updated panel visible. Restarting the Omarchy shell resolved this.
- Visually verified the running panel after restart: standalone PR followed by
  the real stack heading, indented cards, and actual position/size labels.
- Earlier file-copy and rescan checks did not establish live visual acceptance;
  this screenshot inspection does. Live screenshots remain outside the repo.

## Subtle merge-ready accent (2026-09-09)

- Added a faint green card fill and border for fresh, non-draft, mergeable PRs
  with CLEAN merge state, satisfied reviews, and passing CI. Cancelled checks,
  unknown data, stale snapshots, and merge/review blockers suppress the accent.
- QML lint, plugin validation, and all 12 UI checks pass; dark/light previews
  were visually inspected. Keyboard selection retains its existing border.
