// 3D / 5D chip under the input: an explicit print mode for the next message.
import QtQuick 2.7

Rectangle {
    id: chip
    property string label: ""
    property bool selected: false
    signal clicked()
    width: 34; height: 22; radius: 11
    anchors.verticalCenter: parent ? parent.verticalCenter : undefined
    color: selected ? "#1E6FD9" : "#E3EEFC"
    Text { anchors.centerIn: parent; text: chip.label; color: chip.selected ? "white" : "#1E6FD9"; font.pixelSize: 11; font.bold: true }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: chip.clicked() }
}
