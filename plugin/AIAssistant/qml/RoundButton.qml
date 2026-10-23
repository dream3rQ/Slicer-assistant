// Round button of the input card (microphone, send, stop).
import QtQuick 2.7

Rectangle {
    id: button
    property string label: ""
    property string tip: ""
    property color fill: "#EDEFF2"
    property color fg: "#16212C"
    property bool mic: false   // draw a microphone instead of the label
    signal clicked()
    width: 30; height: 30; radius: 15
    color: fill
    opacity: area.pressed ? 0.7 : 1
    Text { anchors.centerIn: parent; text: button.label; color: button.fg; font.pixelSize: 14; font.bold: true; visible: !button.mic }
    Item {  // microphone: capsule, holder arc and stand
        visible: button.mic
        anchors.centerIn: parent
        width: 12; height: 16
        Rectangle { x: 3; width: 6; height: 10; radius: 3; color: button.fg }
        Canvas {
            anchors.fill: parent
            onPaint: {
                var c = getContext("2d");
                c.reset();
                c.strokeStyle = String(button.fg);
                c.lineWidth = 1.4;
                c.beginPath(); c.arc(6, 6.5, 5, 0, Math.PI, false); c.stroke();
                c.beginPath(); c.moveTo(6, 11.5); c.lineTo(6, 15); c.stroke();
            }
        }
    }
    MouseArea { id: area; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: button.clicked() }
}
