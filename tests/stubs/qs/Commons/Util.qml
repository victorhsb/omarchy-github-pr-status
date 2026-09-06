pragma Singleton
import QtQuick
QtObject {
    function alpha(color, opacity) {
        if (typeof color === "string") color = Qt.color(color)
        return Qt.rgba(color.r, color.g, color.b, opacity)
    }
}
