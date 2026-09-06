import QtQuick
import QtTest
import qs.Commons
import qs.Ui as Ui
import "../.." as Plugin

Item {
    width: 640
    height: 680
    Rectangle {
        id: surface
        width: 640
        height: 680
        color: Color.popups.background
        Ui.PanelKeyCatcher {
            id: keys
            anchors.fill: parent
            anchors.margins: 24
            onMoveRequested: function(dx, dy) { content.move(dx, dy) }
            onActivateRequested: content.activate()
            onTabRequested: function(direction) { content.move(0, direction) }
            onTextKey: function(text) { if (text === "r") content.refreshRequested() }
            Plugin.PrContent {
                id: content
                anchors.fill: parent
                loaded: true
            }
        }
    }
    SignalSpy { id: opened; target: content; signalName: "openRequested" }
    SignalSpy { id: closed; target: keys; signalName: "closeRequested" }
    SignalSpy { id: refreshed; target: content; signalName: "refreshRequested" }

    TestCase {
        name: "PrContent"
        when: windowShown

        function row(id, title, draft, review, checks, discussion, inline) {
            let counts = { running: 0, success: 0, skipped: 0, failed: 0, unknown: 0 }
            for (let check of checks) counts[check.bucket]++
            return { id: id, number: Number(id), repository: "example/omarchy-tools", title: title,
                url: "https://github.com/example/omarchy-tools/pull/" + id, draft: draft,
                review: review, discussion: discussion, inline: inline,
                checks: checks, counts: counts, error: null, fetchedAt: 1788662400 }
        }
        function example() {
            return { schemaVersion: 1, account: "octocat", stale: false, partial: false, error: "", lastSuccessAt: 1788662400,
                prs: [
                    row("42", "Add pull request status to the Omarchy bar", false, "Approved", [
                        { name: "Unit tests", status: "SUCCESS", bucket: "success" },
                        { name: "Lint", status: "SUCCESS", bucket: "success" },
                        { name: "Integration tests", status: "IN_PROGRESS", bucket: "running" },
                        { name: "Deploy preview", status: "SKIPPED", bucket: "skipped" }
                    ], 3, 8),
                    row("41", "Improve keyboard navigation in the panel", false, "Changes requested", [
                        { name: "Unit tests", status: "SUCCESS", bucket: "success" },
                        { name: "Lint", status: "FAILURE", bucket: "failed" }
                    ], 2, 5),
                    row("39", "Explore a compact layout for vertical bars", true, "No review decision", [], 0, 0)
                ] }
        }
        function init() {
            opened.clear(); closed.clear(); refreshed.clear()
            content.expandedId = ""; content.selectedId = ""
            content.snapshot = example()
            keys.forceActiveFocus()
            wait(100)
        }

        function test_navigation_and_expand() {
            content.activate()
            compare(opened.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/42")
            content.move(0, 1)
            content.activate()
            compare(opened.signalArguments[1][0], "https://github.com/example/omarchy-tools/pull/41")
            content.move(1, 0)
            compare(content.expandedId, "41")
            content.move(-1, 0)
            compare(content.expandedId, "")
            content.move(0, -1)
            content.move(0, -1)
            content.activate()
            compare(opened.signalArguments[2][0], "https://github.com/example/omarchy-tools/pull/39")
        }
        function test_refresh_preserves_selection() {
            content.move(0, 1)
            let updated = example()
            updated.prs.reverse()
            content.snapshot = updated
            wait(100)
            content.activate()
            compare(opened.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/41")
        }
        function test_initial_and_refresh_layout_do_not_clip_first_pr() {
            let data = example()
            for (let i = 100; i < 115; i++) data.prs.push(row(String(i), "A long pull request title that wraps onto several lines to exercise asynchronous delegate heights and initial list positioning", false, "Review required", [], 1, 2))
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            compare(list.currentIndex, 0)
            verify(Math.abs(list.contentY - list.originY) < 1)
            verify(list.itemAtIndex(0).height > 100)
            content.snapshot = example()
            wait(100)
            verify(Math.abs(list.contentY - list.originY) < 1)
        }
        function test_keyboard_and_title_click() {
            keyClick(Qt.Key_Down)
            keyClick(Qt.Key_Right)
            compare(content.expandedId, "41")
            keyClick(Qt.Key_Return)
            compare(opened.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/41")
            keyClick(Qt.Key_Left)
            compare(content.expandedId, "")
            keyClick(Qt.Key_R)
            compare(refreshed.count, 1)
            keyClick(Qt.Key_Escape)
            compare(closed.count, 1)
            content.move(0, -1)
            wait(100)
            let list = findChild(content, "prList")
            let title = findChild(list.itemAtIndex(0), "prTitle")
            mouseClick(title, 20, title.height / 2)
            compare(opened.signalArguments[1][0], "https://github.com/example/omarchy-tools/pull/42")
        }
        function test_empty_and_error() {
            content.snapshot = { prs: [], stale: true, error: "Sign in with gh auth login." }
            wait(100)
            content.move(0, 1)
            content.activate()
            compare(opened.count, 0)
            verify(surface.visible)
        }
        function test_preview() {
            wait(250)
            let image = grabImage(surface)
            verify(image.width > 0)
            image.save(Qt.resolvedUrl("../../preview.png").toString().replace("file://", ""))
        }
        function test_preview_light() {
            Color.popups.background = "#f4f5f8"
            Color.popups.text = "#263044"
            Color.muted = "#768196"
            Color.accent = "#4d689e"
            Color.urgent = "#b1394b"
            wait(100)
            let image = grabImage(surface)
            image.save(Qt.resolvedUrl("../../preview-light.png").toString().replace("file://", ""))
            Color.popups.background = "#171b24"
            Color.popups.text = "#e0e4ec"
            Color.muted = "#929cae"
            Color.accent = "#98b8ed"
            Color.urgent = "#e47d85"
        }
    }
}
