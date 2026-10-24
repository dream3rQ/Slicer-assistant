// The 3D / 5D switch in the input card: one pill with two segments. `mode` is
// "", "3D" or "5D"; clicking the active segment clears it (the agent then chooses).
import QtQuick 2.7

Rectangle {
    id: pill
    property string mode: ""
    signal picked(string mode)
    Style { id: st }
    width: 64; height: 23; radius: 12
    color: st.accentSoft

    Rectangle {  // the active segment
        visible: pill.mode !== ""
        x: pill.mode === "5D" ? 31 : 2
        y: 2
        width: 31; height: 19; radius: 10
        color: st.accent
        Behavior on x { NumberAnimation { duration: 120 } }
    }
    Row {
        x: 2; y: 2
        Repeater {
            model: ["3D", "5D"]
            delegate: Item {
                width: 31; height: 19
                Text {
                    anchors.centerIn: parent
                    text: modelData
                    color: pill.mode === modelData ? "white" : st.accent
                    font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: pill.picked(pill.mode === modelData ? "" : modelData)
                }
            }
        }
    }
}
