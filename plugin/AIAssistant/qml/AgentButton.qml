// Toggle for the Stereo Agent panel, in the slicer's top bar left of the view
// orientation buttons (slicer_ui.mount sets topbar and orientation).
import QtQuick 2.7

Rectangle {
    id: base
    property var topbar: null
    property var orientation: null
    property var panel: null
    readonly property bool open: panel !== null && panel.visible

    width: 36
    height: 36
    radius: 18
    x: orientation ? orientation.x - width - 16 : (parent ? parent.width - 480 : 0)
    anchors.verticalCenter: parent ? parent.verticalCenter : undefined
    color: open ? "#1E6FD9" : (hover.containsMouse ? "#2B3442" : "#16212C")
    z: 10

    Text {
        anchors.centerIn: parent
        text: "AI"
        color: "white"
        font.bold: true
        font.pixelSize: 13
    }

    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: manager.toggle()
    }
}
