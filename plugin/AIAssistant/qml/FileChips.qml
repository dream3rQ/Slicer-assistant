// Attached files as chips: in the composer (with ×) and in a sent message.
// Long names are elided so the chips wrap with the panel's width.
import QtQuick 2.7

Flow {
    id: chips
    property var names: []
    property var ids: []          // empty in a sent message: no × then
    property color fill: "#E3EEFC"
    property var panel: null      // the panel, for its font and colours
    signal remove(string id)
    spacing: 6

    Repeater {
        model: chips.names
        delegate: Rectangle {
            property bool removable: chips.ids.length > index
            height: 26; radius: 13
            color: chips.fill
            border.color: chips.panel ? chips.panel.blueLine : "#BBD7F5"
            width: Math.min(chips.width, icon.width + label.implicitWidth + (removable ? 44 : 26))
            Text {
                id: icon
                x: 9; anchors.verticalCenter: parent.verticalCenter
                text: /\.(png|jpe?g|webp)$/i.test(modelData) ? "▣" : /\.(stl|obj|3mf)$/i.test(modelData) ? "◆" : "≡"
                color: chips.panel ? chips.panel.blue : "#1E6FD9"
                font.pixelSize: 11
            }
            Text {
                id: label
                anchors.left: icon.right; anchors.leftMargin: 6
                anchors.right: parent.right; anchors.rightMargin: removable ? 26 : 10
                anchors.verticalCenter: parent.verticalCenter
                text: modelData
                elide: Text.ElideMiddle
                color: chips.panel ? chips.panel.blue : "#1E6FD9"
                font.family: chips.panel ? chips.panel.fontFamily : ""
                font.pixelSize: chips.panel ? chips.panel.fs.small : 11
            }
            Text {
                visible: removable
                anchors.right: parent.right; anchors.rightMargin: 9; anchors.verticalCenter: parent.verticalCenter
                text: "×"; color: chips.panel ? chips.panel.blue : "#1E6FD9"; font.pixelSize: 15
                MouseArea { anchors.fill: parent; anchors.margins: -5; cursorShape: Qt.PointingHandCursor; onClicked: chips.remove(chips.ids[index]) }
            }
        }
    }
}
