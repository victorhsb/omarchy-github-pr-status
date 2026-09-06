pragma Singleton
import QtQuick
QtObject {
    property color foreground: "#e0e4ec"
    property color background: "#171b24"
    property color accent: "#98b8ed"
    property color muted: "#929cae"
    property color urgent: "#e47d85"
    property QtObject popups: QtObject {
        property color text: "#e0e4ec"
        property color background: "#171b24"
    }
}
