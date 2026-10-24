// Attached files as chips: in the input card (with ×) and in a sent message.
// The mock-up has none; built from its tokens (spec section 8). Long names are
// elided in the middle so the chips wrap at any panel width.
import QtQuick 2.7

Flow {
    id: chips
    property var names: []
    property var ids: []          // empty in a sent message: no × then
    signal remove(string id)
    Style { id: st }
    spacing: 6

    Repeater {
        model: chips.names
        delegate: Rectangle {
            property bool removable: chips.ids.length > index
            height: 24; radius: 6
            color: "white"
            border.color: st.borderAccent
            border.width: 1
            width: Math.min(chips.width, 8 + 14 + 6 + label.implicitWidth + (removable ? 26 : 10))
            Icon { id: icon; x: 8; anchors.verticalCenter: parent.verticalCenter; name: "file_check"; width: 14; height: 14 }
            Text {
                id: label
                anchors.left: icon.right; anchors.leftMargin: 6
                anchors.right: parent.right; anchors.rightMargin: removable ? 26 : 10
                anchors.verticalCenter: parent.verticalCenter
                text: modelData
                elide: Text.ElideMiddle
                color: st.accent
                font.family: st.family; font.pixelSize: 11; font.weight: Font.Medium
            }
            Icon {
                visible: removable
                anchors.right: parent.right; anchors.rightMargin: 9; anchors.verticalCenter: parent.verticalCenter
                name: "close"; width: 8; height: 8
                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: chips.remove(chips.ids[index]) }
            }
        }
    }
}
