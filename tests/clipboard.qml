import QtQuick
import Quickshell
import ".." as Plugin

Item {
    width: 640
    height: 680
    Plugin.Panel {
        id: panel
        hostWidget: QtObject {
            property bool loaded: true
            property bool busy: false
            property var snapshot: ({ prs: [{ id: "42", number: 42,
                repository: "example/tools", url: "https://github.com/example/tools/pull/42",
                title: "Fictional PR", review: "Review required", checks: [], counts: {}, draft: false }] })
        }
    }
    Timer {
        interval: 50
        running: true
        repeat: true
        property int step: 0
        property int ticks: 0
        onTriggered: {
            function findContent(item) {
                if (item.objectName === "prContent") return item
                for (let child of item.children) {
                    let found = findContent(child)
                    if (found) return found
                }
                return null
            }
            let content = findContent(panel)
            if (!content || ++ticks > 100) {
                console.error("Clipboard test timed out")
                Qt.exit(1)
                return
            }
            let missing = Quickshell.env("TEST_COPY_MISSING") === "1"
            let expected = missing ? ["Could not copy"] : ["URL copied", "Reference copied", "Could not copy", "Reference copied"]
            if (step % 2 === 0) {
                if (step === 4) panel.hostWidget.snapshot.prs[0].url = "rejected"
                content.copySelected(step === 2 || step === 6)
                step++
            } else if (!content.copyBusy) {
                if (content.copyFeedback !== expected[(step - 1) / 2]) {
                    console.error("Unexpected clipboard result:", content.copyFeedback)
                    Qt.exit(1)
                    return
                }
                step++
                if (step === expected.length * 2) {
                    console.log("Clipboard process checks passed")
                    Qt.quit()
                }
            }
        }
    }
}
