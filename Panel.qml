import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.Panel {
    id: root
    moduleName: "torugo.github-pr-status"
    manageIpc: false
    property var anchorItem: null
    property var hostWidget: null

    function open() { root.controller.show() }
    function close() { root.controller.hide() }
    function openPr(url) {
        if (/^https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/\d+$/.test(url))
            Qt.openUrlExternally(url)
    }

    Process {
        id: copier
        property bool compact: false
        // Defer the fallback so normal exits can report their result first.
        onRunningChanged: if (!running) Qt.callLater(function() {
            if (!copier.running) content.copyFinished(false, copier.compact)
        })
        // qmllint disable signal-handler-parameters
        onExited: function(exitCode, exitStatus) {
            content.copyFinished(exitCode === 0 && exitStatus === 0, compact)
        }
        // qmllint enable signal-handler-parameters
    }

    Ui.KeyboardPanel {
        id: panel
        anchorItem: root.anchorItem
        owner: root.hostWidget || root
        bar: root.bar
        open: root.opened
        focusTarget: keys
        contentWidth: panel.fittedContentWidth(Style.space(580))
        contentHeight: panel.fittedContentHeight(Style.space(580))

        Ui.PanelKeyCatcher {
            id: keys
            Keys.forwardTo: [content]
            anchors.fill: parent
            onCloseRequested: root.close()
            onMoveRequested: function(dx, dy) { content.move(dx, dy) }
            onActivateRequested: content.activate()
            onTabRequested: function(direction) { content.move(0, direction) }
            onTextKey: function(text) { if (text.toLowerCase() === "r") content.refreshRequested() }
            PrContent {
                id: content
                objectName: "prContent"
                anchors.fill: parent
                snapshot: root.hostWidget ? root.hostWidget.snapshot : ({ prs: [] })
                loaded: root.hostWidget ? root.hostWidget.loaded : false
                busy: root.hostWidget ? root.hostWidget.busy : false
                onOpenRequested: function(url) { root.openPr(url) }
                onRefreshRequested: if (root.hostWidget) root.hostWidget.refresh(true)
                onCopyRequested: function(text, compact) {
                    copier.compact = compact
                    copier.command = ["wl-copy", "--type", "text/plain", "--", text]
                    copier.running = true
                    Qt.callLater(function() {
                        if (!copier.running) content.copyFinished(false, compact)
                    })
                }
            }
        }
    }
}
