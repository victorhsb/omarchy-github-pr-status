import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.BarWidget {
    id: root
    moduleName: "torugo.github-pr-status"

    property var snapshot: ({ prs: [], error: "", stale: false })
    property bool loaded: false
    readonly property bool opened: panelLoader.item ? panelLoader.item.opened : false
    readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing : false
    readonly property bool runningChecks: snapshot.prs.some(function(pr) { return pr.counts && pr.counts.running > 0 })
    readonly property bool failedChecks: snapshot.prs.some(function(pr) { return pr.counts && pr.counts.failed > 0 })
    readonly property int refreshInterval: opened && runningChecks ? 20 : 60
    readonly property bool busy: fetcher.running

    function open() { if (panelLoader.item) panelLoader.item.open() }
    function close() { if (panelLoader.item) panelLoader.item.close() }
    function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
    function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }
    function injectPanel() {
        if (!panelLoader.item) return
        panelLoader.item.bar = root.bar
        panelLoader.item.anchorItem = button
        panelLoader.item.hostWidget = root
    }
    function refresh(force) {
        if (fetcher.running) return
        let path = decodeURIComponent(Qt.resolvedUrl("bin/github_pr_status.py").toString().replace(/^file:\/\//, ""))
        let args = ["python3", path, "--notify", "--interval", String(refreshInterval)]
        if (force) args.push("--force")
        fetcher.command = args
        fetcher.running = true
    }
    function fetchFailed() {
        let next = Object.assign({}, snapshot)
        next.stale = true
        next.error = "Could not load PR status. Check that Python 3 and gh are installed."
        snapshot = next
        loaded = true
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    onBarChanged: injectPanel()
    onOpenedChanged: if (opened) refresh(false)
    Component.onCompleted: refresh(false)

    Loader {
        id: panelLoader
        active: true
        visible: false
        source: Qt.resolvedUrl("Panel.qml")
        onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
    }
    Timer {
        interval: root.refreshInterval * 1000
        running: true
        repeat: true
        onTriggered: root.refresh(false)
    }
    Process {
        id: fetcher
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    let result = JSON.parse(text)
                    if (result.schemaVersion !== 1 || !Array.isArray(result.prs)) throw new Error("Invalid snapshot")
                    root.snapshot = result
                    root.loaded = true
                } catch (error) { root.fetchFailed() }
            }
        }
        // Quickshell's qmltypes omits QProcess::ExitStatus, although the signal works at runtime.
        // qmllint disable signal-handler-parameters
        onExited: function(exitCode) {
            if (exitCode !== 0) root.fetchFailed()
        }
        // qmllint enable signal-handler-parameters
    }
    Ui.WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "\uf407" + (root.vertical ? "\n" : " ") + (!root.loaded ? "…" : String(root.snapshot.prs.length))
        fixedHeight: root.vertical ? Style.space(48) : -1
        foreground: root.failedChecks ? Color.urgent : root.runningChecks ? Color.accent : root.bar ? root.bar.barForeground : Color.foreground
        tooltipText: "GitHub PR Status · " + (!root.loaded ? "Loading" : root.snapshot.prs.length + " open PRs")
            + (root.snapshot.stale ? " · Stale / incomplete" : "")
            + (root.failedChecks ? " · Failed checks" : root.runningChecks ? " · Checks running" : "")
        onPressed: function(code) {
            if (code === Qt.LeftButton) root.toggle()
            if (code === Qt.RightButton) root.refresh(true)
        }
        Rectangle {
            width: Style.space(5)
            height: width
            radius: width / 2
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Style.space(3)
            visible: root.snapshot.stale || root.runningChecks || root.failedChecks
            color: root.snapshot.stale ? Color.muted : root.failedChecks ? Color.urgent : Color.accent
            SequentialAnimation on opacity {
                running: root.runningChecks
                loops: Animation.Infinite
                NumberAnimation { to: 0.3; duration: 700 }
                NumberAnimation { to: 1; duration: 700 }
            }
        }
    }
}
