pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import qs.Commons

Rectangle {
    id: root
    required property var pr
    property bool selected: false
    property bool expanded: false
    signal openRequested()
    signal expandRequested()
    readonly property color foreground: Color.popups.text
    readonly property color muted: Util.alpha(Color.popups.text, 0.68)
    readonly property string family: Style.font.family
    readonly property bool lightSurface: (Color.popups.background.r * 0.299 + Color.popups.background.g * 0.587 + Color.popups.background.b * 0.114) > 0.6
    readonly property var colors: ({ running: lightSurface ? "#926000" : "#d5a44c", success: lightSurface ? "#337745" : "#78ad79", skipped: Color.muted, failed: Color.urgent, unknown: Color.muted })
    readonly property int total: pr.checks ? pr.checks.length : 0
    readonly property string summary: !pr.counts ? "Unknown" : total === 0 ? "No checks" : ["running", "success", "skipped", "failed", "unknown"].filter(function(key) { return pr.counts[key] > 0 }).map(function(key) { return pr.counts[key] + " " + key }).join("  ·  ")

    height: body.implicitHeight + Style.space(24)
    radius: Style.space(10)
    color: Util.alpha(Color.popups.text, selected ? 0.07 : 0.025)
    border.width: 1
    border.color: Util.alpha(selected ? Color.accent : Color.muted, selected ? 0.6 : 0.18)

    Column {
        id: body
        x: Style.space(12)
        y: Style.space(12)
        width: parent.width - Style.space(24)
        spacing: Style.space(8)
        Row {
            width: parent.width
            Text {
                width: parent.width - badge.width - Style.space(8)
                text: root.pr.repository + "  #" + root.pr.number
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: root.muted
                font.family: root.family
                font.pixelSize: Style.font.bodySmall
            }
            Rectangle {
                id: badge
                width: badgeText.implicitWidth + Style.space(14)
                height: badgeText.implicitHeight + Style.space(4)
                radius: height / 2
                color: Util.alpha(root.pr.draft ? Color.muted : root.colors.success, 0.15)
                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: root.pr.draft ? "Draft" : "Open"
                    color: root.pr.draft ? root.muted : root.colors.success
                    font.family: root.family
                    font.pixelSize: Style.font.bodySmall
                }
            }
        }
        Text {
            objectName: "prTitle"
            width: parent.width
            text: root.pr.title
            textFormat: Text.PlainText
            color: root.foreground
            font.family: root.family
            font.pixelSize: Style.font.body
            font.bold: true
            wrapMode: Text.WordWrap
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.openRequested()
            }
        }
        Flow {
            width: parent.width
            spacing: Style.space(12)
            Text {
                text: root.pr.review
                textFormat: Text.PlainText
                font.family: root.family
                font.pixelSize: Style.font.bodySmall
                color: root.pr.review === "Approved" ? root.colors.success : root.pr.review === "Changes requested" ? Color.urgent : root.foreground
            }
            Text {
                text: "Discussion " + (root.pr.discussion === null ? "—" : root.pr.discussion)
                font.family: root.family
                font.pixelSize: Style.font.bodySmall
                color: root.muted
            }
            Text {
                text: "Inline " + (root.pr.inline === null ? "—" : root.pr.inline)
                font.family: root.family
                font.pixelSize: Style.font.bodySmall
                color: root.muted
            }
        }
        Rectangle {
            width: parent.width
            height: Style.space(5)
            radius: height / 2
            color: Util.alpha(Color.muted, 0.2)
            Row {
                anchors.fill: parent
                Repeater {
                    model: ["running", "success", "skipped", "failed", "unknown"]
                    Rectangle {
                        required property string modelData
                        height: Style.space(5)
                        width: root.total && root.pr.counts ? body.width * root.pr.counts[modelData] / root.total : 0
                        color: root.colors[modelData]
                    }
                }
            }
            MouseArea { id: barHover; anchors.fill: parent; hoverEnabled: true; onClicked: root.expandRequested() }
            ToolTip.visible: barHover.containsMouse
            ToolTip.text: root.summary
        }
        Text {
            width: parent.width
            text: root.summary + (root.total ? root.expanded ? "   ▴ Hide checks" : "   ▾ Show checks" : "")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: root.muted
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.expandRequested()
            }
        }
        Text {
            width: parent.width
            visible: !!root.pr.error
            text: "Stale / unavailable · " + (root.pr.error || "")
                + (root.pr.fetchedAt ? " · Last updated " + Qt.formatDateTime(new Date(root.pr.fetchedAt * 1000), "HH:mm:ss") : "")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: Color.urgent
        }
        Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.expanded
            Repeater {
                model: root.expanded && root.pr.checks ? root.pr.checks : []
                Text {
                    required property var modelData
                    width: body.width
                    text: "●  " + modelData.name + "  ·  " + modelData.status
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    font.family: root.family
                    font.pixelSize: Style.font.bodySmall
                    color: root.colors[modelData.bucket]
                }
            }
        }
    }
}
