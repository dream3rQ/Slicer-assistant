// Round button of the input card: an icon from qml/icons, or a text label.
import QtQuick 2.7

Rectangle {
    id: button
    property string icon: ""
    property real iconWidth: 13
    property real iconHeight: 18
    property string label: ""
    property string tip: ""
    property color fill: "#EDEFF2"
    property color fg: "#292727"
    property bool spinning: false   // a turning arc instead of the icon (speech is being recognised)
    signal clicked()
    Style { id: st }
    width: 33; height: 33; radius: width / 2
    color: fill
    opacity: area.pressed ? 0.7 : 1
    Icon { visible: button.icon !== "" && !button.spinning; anchors.centerIn: parent; name: button.icon; width: button.iconWidth; height: button.iconHeight }
    Canvas {
        id: spinner
        visible: button.spinning
        anchors.centerIn: parent
        width: 18; height: 18
        onPaint: {
            var c = getContext("2d");
            c.reset();
            c.lineWidth = 2;
            c.lineCap = "round";
            c.strokeStyle = String(st.accent);
            c.beginPath();
            c.arc(9, 9, 7, 0, Math.PI * 1.5, false);
            c.stroke();
        }
        RotationAnimation on rotation { running: button.spinning; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
    }
    Text { visible: button.icon === "" && !button.spinning; anchors.centerIn: parent; text: button.label; color: button.fg; font.family: st.family; font.pixelSize: 13; font.weight: Font.DemiBold }
    MouseArea { id: area; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: button.clicked() }
}
