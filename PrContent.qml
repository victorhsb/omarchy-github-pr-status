pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import qs.Commons

Item {
    id: root
    property var snapshot: ({ prs: [] })
    property bool loaded: false
    property bool busy: false
    property string expandedId: ""
    property string selectedId: ""
    property bool copyBusy: false
    property string copyFeedback: ""
    readonly property color foreground: Color.popups.text
    readonly property color muted: Util.alpha(Color.popups.text, 0.68)
    readonly property string family: Style.font.family
    signal openRequested(string url)
    signal refreshRequested()
    signal copyRequested(string text, bool compact)

    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_C && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))) {
            if (!event.isAutoRepeat) copySelected(!!(event.modifiers & Qt.ShiftModifier))
            event.accepted = true
        }
    }
    function copySelected(compact) {
        if (copyBusy || !snapshot.prs.length) return
        let pr = snapshot.prs[Math.max(0, list.currentIndex)]
        if (!pr) return
        let text = compact ? pr.repository + "#" + pr.number : pr.url
        copyFeedbackTimer.stop()
        copyFeedback = ""
        copyBusy = true
        copyRequested(text, compact)
    }
    function copyFinished(success, compact) {
        if (!copyBusy) return
        copyBusy = false
        copyFeedback = success ? (compact ? "Reference copied" : "URL copied") : "Could not copy"
        copyFeedbackTimer.restart()
    }
    Timer {
        id: copyFeedbackTimer
        interval: 2500
        onTriggered: root.copyFeedback = ""
    }

    function move(dx, dy) {
        if (!list.count) return
        if (dy) {
            list.currentIndex = (Math.max(0, list.currentIndex) + dy + list.count) % list.count
            selectedId = snapshot.prs[list.currentIndex].id
            list.positionViewAtIndex(list.currentIndex, ListView.Contain)
        }
        if (dx) expandedId = dx > 0 ? snapshot.prs[Math.max(0, list.currentIndex)].id : ""
    }
    function activate() {
        if (list.count) openRequested(snapshot.prs[Math.max(0, list.currentIndex)].url)
    }
    onSnapshotChanged: {
        let index = snapshot.prs.findIndex(function(pr) { return pr.id === root.selectedId })
        Qt.callLater(function() {
            list.currentIndex = Math.max(0, index)
            list.forceLayout()
            list.positionViewAtIndex(list.currentIndex, list.currentIndex === 0 ? ListView.Beginning : ListView.Contain)
        })
    }

    Column {
        id: heading
        width: parent.width
        spacing: Style.space(7)
        Row {
            width: parent.width
            Text {
                width: parent.width - refresh.width
                text: "Pull requests"
                textFormat: Text.PlainText
                font.family: root.family
                font.pixelSize: Style.font.title
                font.bold: true
                color: root.foreground
            }
            Text {
                id: refresh
                text: root.busy ? "Refreshing…" : "↻ Refresh"
                textFormat: Text.PlainText
                font.family: root.family
                font.pixelSize: Style.font.body
                color: Color.accent
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    enabled: !root.busy
                    onClicked: root.refreshRequested()
                }
            }
        }
        Text {
            width: parent.width
            text: root.snapshot.account ? "@" + root.snapshot.account + "  ·  " + root.snapshot.prs.length + " open, including drafts" : "Your authored PRs · github.com"
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.family: root.family
            font.pixelSize: Style.font.body
            color: root.muted
        }
        Text {
            width: parent.width
            visible: !!root.snapshot.error
            text: (root.snapshot.partial ? "Incomplete · " : "") + (root.snapshot.error || "")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: Color.urgent
        }
        Rectangle { width: parent.width; height: 1; color: root.muted; opacity: 0.25 }
    }

    ListView {
        id: list
        objectName: "prList"
        anchors.top: heading.bottom
        anchors.bottom: footer.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.topMargin: Style.space(12)
        anchors.bottomMargin: Style.space(12)
        clip: true
        spacing: Style.space(10)
        model: root.snapshot.prs
        currentIndex: 0
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        delegate: PrRow {
            required property var modelData
            required property int index
            width: list.width - Style.space(12)
            pr: modelData
            stale: !!root.snapshot.stale || !!root.snapshot.partial
            stackStart: !!modelData.stack && (index === 0
                || !root.snapshot.prs[index - 1].stack
                || root.snapshot.prs[index - 1].repository !== modelData.repository
                || root.snapshot.prs[index - 1].stack.number !== modelData.stack.number)
            stackEnd: !!modelData.stack && (index === root.snapshot.prs.length - 1
                || !root.snapshot.prs[index + 1].stack
                || root.snapshot.prs[index + 1].repository !== modelData.repository
                || root.snapshot.prs[index + 1].stack.number !== modelData.stack.number)
            selected: list.currentIndex === index
            expanded: root.expandedId === modelData.id
            onOpenRequested: { list.currentIndex = index; root.selectedId = modelData.id; root.openRequested(modelData.url) }
            onExpandRequested: {
                list.currentIndex = index
                root.selectedId = modelData.id
                root.expandedId = expanded ? "" : modelData.id
            }
        }
        Text {
            anchors.centerIn: parent
            width: parent.width - Style.space(30)
            visible: list.count === 0
            text: !root.loaded ? "Loading your pull requests…" : root.snapshot.error ? "Pull requests are unavailable.\nUse Refresh to try again." : "All clear.\nYou have no open pull requests."
            textFormat: Text.PlainText
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.body
            color: root.muted
        }
    }
    Column {
        id: footer
        anchors.bottom: parent.bottom
        width: parent.width
        spacing: Style.space(5)
        Text {
            width: parent.width
            text: root.snapshot.lastSuccessAt ? (root.snapshot.stale ? "Last complete update " : "Updated ") + Qt.formatDateTime(new Date(root.snapshot.lastSuccessAt * 1000), "MMM d, HH:mm:ss") : root.snapshot.fetchedAt ? "No complete update yet" : ""
            textFormat: Text.PlainText
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: root.muted
        }
        Text {
            width: parent.width
            text: "↑↓ navigate   ·   ←→ checks   ·   enter opens\nc copy URL   ·   shift+c copy reference   ·   r refresh   ·   esc closes"
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: root.muted
        }
    }
    Rectangle {
        id: copyToast
        objectName: "copyToast"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: footer.top
        anchors.bottomMargin: Style.space(12)
        width: Math.min(parent.width, toastText.implicitWidth + Style.space(28))
        height: toastText.implicitHeight + Style.space(18)
        radius: Style.space(8)
        color: Color.popups.background
        border.color: Color.accent
        opacity: root.copyFeedback ? 1 : 0
        visible: opacity > 0
        z: 1
        Behavior on opacity { NumberAnimation { duration: 150 } }
        Text {
            id: toastText
            anchors.centerIn: parent
            // Keep the last message while the toast fades out.
            property string message: ""
            Connections {
                target: root
                function onCopyFeedbackChanged() {
                    if (root.copyFeedback) toastText.message = root.copyFeedback
                }
            }
            text: message
            textFormat: Text.PlainText
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: root.foreground
        }
    }
}
