// A flat icon button for the panel header.
import QtQuick 2.7

Rectangle {
    id: button
    property string label: ""
    property string tip: ""
    signal clicked()
    width: 28; height: 28; radius: 6
    color: area.containsMouse ? "#E3EEFC" : "transparent"
    Text { anchors.centerIn: parent; text: button.label; color: "#5A6878"; font.pixelSize: 15 }
    MouseArea { id: area; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: button.clicked() }
}
