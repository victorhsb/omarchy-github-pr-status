pragma Singleton
import QtQuick
QtObject {
    function space(value) { return value }
    property QtObject font: QtObject {
        property string family: "monospace"
        property int title: 20
        property int body: 14
        property int bodySmall: 12
    }
}
