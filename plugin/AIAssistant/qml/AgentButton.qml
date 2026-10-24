// The "AI" tab at the right edge of the 3D view, under the top bar: opens the
// Stereo Agent panel and hides while it is open (the panel's own header closes it).
// slicer_ui.mount puts it next to the top bar and sets `topbar`.
import QtQuick 2.7

Item {
    id: base
    property var topbar: null
    property var orientation: null
    property var panel: null
    readonly property bool open: panel !== null && panel.visible
    Style { id: st }

    width: 50
    height: 41
    x: parent ? parent.width - width : 0
    y: topbar ? topbar.y + topbar.height + 6 : 6
    z: 50
    visible: !open
    clip: true

    Rectangle {  // right corners and border run off the window edge, as in the mock-up
        width: parent.width + 6
        height: parent.height
        radius: 5
        color: hover.containsMouse ? st.accentSoft : "white"
        border.color: st.accentDeep
        border.width: 1
    }
    Text {
        anchors.centerIn: parent
        text: "AI"
        color: st.accentDeep
        font.family: st.family
        font.pixelSize: 16
    }
    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: manager.toggle()
    }
}
