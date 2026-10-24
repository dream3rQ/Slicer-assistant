// A collapsible group of ready prompts on the panel's empty state. Each prompt
// shows a short label (as in the mock-up) and puts its full text into the input.
import QtQuick 2.7

Rectangle {
    id: group
    property string title: ""
    property var prompts: []      // [{ label, text }]
    property bool expanded: false
    signal picked(string text)
    Style { id: st }
    width: parent ? parent.width : 278
    height: column.height + (expanded ? 22 : 0)
    radius: 6
    color: "white"
    border.color: st.borderAccent
    border.width: 1

    Column {
        id: column
        x: 10; y: expanded ? 11 : 0
        width: parent.width - 20
        spacing: 8

        Item {
            width: parent.width
            height: group.expanded ? 14 : 36
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: group.title
                color: st.accent
                font.family: st.family; font.pixelSize: 11; font.weight: Font.Medium
            }
            Icon {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                name: "chevron_small"; width: 9; height: 5
                rotation: group.expanded ? 180 : 0
            }
            MouseArea { anchors.fill: parent; anchors.margins: group.expanded ? -11 : 0; cursorShape: Qt.PointingHandCursor; onClicked: group.expanded = !group.expanded }
        }
        Column {
            visible: group.expanded
            width: parent.width
            Repeater {
                model: group.expanded ? group.prompts : []
                delegate: Rectangle {
                    width: parent.width; height: 22; radius: 4
                    color: area.containsMouse ? st.accentSoft : "transparent"
                    Text {
                        x: 6; anchors.verticalCenter: parent.verticalCenter
                        text: modelData.label
                        color: area.containsMouse ? st.accent : st.textSecondary
                        font.family: st.family; font.pixelSize: 11
                    }
                    MouseArea { id: area; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: group.picked(modelData.text) }
                }
            }
        }
    }
}
