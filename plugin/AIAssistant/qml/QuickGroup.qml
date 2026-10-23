// A collapsible group of ready prompts on the panel's empty state.
import QtQuick 2.7

Column {
    id: group
    property string title: ""
    property var prompts: []
    property bool expanded: false
    signal picked(string text)
    width: parent ? parent.width : 280
    spacing: 4

    Rectangle {
        width: parent.width; height: 34; radius: 6
        color: "white"
        border.color: "#9CC3F0"
        Text { x: 10; anchors.verticalCenter: parent.verticalCenter; text: group.title; color: "#1E6FD9"; font.pixelSize: 12 }
        Text { anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; text: group.expanded ? "▴" : "▾"; color: "#1E6FD9"; font.pixelSize: 14 }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: group.expanded = !group.expanded }
    }
    Repeater {
        model: group.expanded ? group.prompts : []
        delegate: Rectangle {
            width: group.width; height: promptText.implicitHeight + 14; radius: 6
            color: area.containsMouse ? "#E3EEFC" : "transparent"
            Text { id: promptText; x: 12; width: parent.width - 24; anchors.verticalCenter: parent.verticalCenter; text: modelData; wrapMode: Text.Wrap; color: "#16212C"; font.pixelSize: 12 }
            MouseArea { id: area; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: group.picked(modelData) }
        }
    }
}
