// Buttons under a warning in the chat (mock-up frames 5-6): primary (blue),
// secondary (white with a border) and tertiary (grey-blue).
import QtQuick 2.7

Rectangle {
    id: button
    property string label: ""
    property string kind: "primary"
    signal clicked()
    Style { id: st }
    width: text.implicitWidth + 20
    height: 30
    radius: 6
    color: kind === "primary" ? st.accent : kind === "secondary" ? "white" : st.tertiaryBg
    border.color: kind === "secondary" ? st.border : "transparent"
    border.width: kind === "secondary" ? 1 : 0
    opacity: area.pressed ? 0.75 : 1
    Text {
        id: text
        anchors.centerIn: parent
        text: button.label
        color: button.kind === "primary" ? "white" : button.kind === "secondary" ? st.textPrimary : st.accent
        font.family: st.family; font.pixelSize: 12; font.weight: Font.DemiBold
    }
    MouseArea { id: area; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: button.clicked() }
}
