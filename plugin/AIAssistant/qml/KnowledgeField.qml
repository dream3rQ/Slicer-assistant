// One field of the material form in KnowledgeWindow.qml. `col` is knowledge_ui.column():
// { key, label, kind: id|text|num|scale|bool|choice, unit?, options? }. The field shows
// `value` and reports changes with edited(value); the text inputs take `value` only when
// `version` changes (another material loaded), so typing is never overwritten.
import QtQuick 2.7

Column {
    id: field
    property var col: ({})
    property int version: 0
    property string value: ""
    property string error: ""
    signal edited(string value)
    Style { id: st }
    spacing: 4

    readonly property bool typed: col.kind === "id" || col.kind === "text" || col.kind === "num"
    // the segments of scale / choice: [{ value, label }]; "" is "не указано"
    readonly property var segments: col.kind === "scale"
        ? [{ value: "", label: "—" }, { value: "0", label: "0" }, { value: "1", label: "1" }, { value: "2", label: "2" }]
        : col.kind === "choice" ? [{ value: "", label: "—" }].concat(col.options || []) : []

    // what the field shows: win.vals is edited in place (no change signal), so the field
    // keeps its own copy, taken from `value` on load and updated on every edit
    property string current: ""
    function reset() { current = value; if (typed) input.text = value; }
    function set(v) { if (v === current) return; current = v; edited(v); }
    onVersionChanged: reset()
    Component.onCompleted: reset()

    Text {
        width: parent.width; elide: Text.ElideRight
        text: field.col.label + (field.col.kind === "id" ? " (латиница, цифры, _)" : "")
        color: st.textSecondary
        font.family: st.family; font.pixelSize: 11
    }

    // id / text / num
    Rectangle {
        visible: field.typed
        width: parent.width; height: 30; radius: 6
        border.width: 1
        border.color: field.error ? st.danger : input.activeFocus ? st.borderAccent : st.border
        color: field.error ? st.dangerBg : "white"
        TextInput {
            id: input
            x: 10; width: parent.width - 20 - unit.width
            anchors.verticalCenter: parent.verticalCenter
            color: st.textPrimary
            font.family: st.family; font.pixelSize: 12
            selectByMouse: true; clip: true
            onTextChanged: field.set(text)
        }
        Text {
            id: unit
            visible: field.col.kind === "num" && !!field.col.unit
            width: visible ? implicitWidth + 10 : 0
            anchors.right: parent.right; anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignRight
            text: field.col.unit || ""
            color: st.textSecondary
            font.family: st.family; font.pixelSize: 11
        }
    }

    // scale / choice: a segmented control
    Rectangle {
        visible: field.segments.length > 0
        width: parent.width; height: 30; radius: 6
        border.width: 1; border.color: field.error ? st.danger : st.border
        color: field.error ? st.dangerBg : "white"
        clip: true
        Row {
            anchors.fill: parent; anchors.margins: 1
            Repeater {
                model: field.segments
                delegate: Rectangle {
                    readonly property bool on: field.current === modelData.value
                    width: (parent.width) / field.segments.length; height: parent.height
                    radius: 5
                    color: on ? st.accentSoft : "transparent"
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 6; elide: Text.ElideRight
                        horizontalAlignment: Text.AlignHCenter
                        text: modelData.label
                        color: parent.on ? st.accent : st.textPrimary
                        font.family: st.family; font.pixelSize: 11; font.weight: parent.on ? Font.DemiBold : Font.Normal
                    }
                    Rectangle { visible: index > 0; width: 1; height: parent.height; color: st.border }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: field.set(modelData.value) }
                }
            }
        }
    }

    // bool: a switch, like the rule switch in MemoryWindow.qml
    Item {
        visible: field.col.kind === "bool"
        width: parent.width; height: 30
        readonly property bool on: field.current === "1"
        Rectangle {
            id: track
            anchors.verticalCenter: parent.verticalCenter
            width: 32; height: 18; radius: 9
            color: parent.on ? st.accent : st.tertiaryBg
            border.width: 1; border.color: field.error ? st.danger : parent.on ? st.accent : st.border
            Rectangle {
                x: parent.parent.on ? parent.width - width - 2 : 2
                anchors.verticalCenter: parent.verticalCenter
                width: 14; height: 14; radius: 7
                color: "white"
            }
        }
        Text {
            x: track.width + 8
            anchors.verticalCenter: parent.verticalCenter
            text: field.current === "" ? "не указано" : parent.on ? "да" : "нет"
            color: field.current === "" ? st.iconMuted : st.textPrimary
            font.family: st.family; font.pixelSize: 12
        }
        MouseArea {
            width: track.width + 60; height: parent.height
            cursorShape: Qt.PointingHandCursor
            onClicked: field.set(parent.on ? "0" : "1")
        }
    }

    Text {
        visible: field.error !== ""
        width: parent.width; wrapMode: Text.Wrap
        text: field.error
        color: st.danger
        font.family: st.family; font.pixelSize: 11
    }
}
