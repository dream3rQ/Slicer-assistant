// The plan card over the 3D view, next to the Stereo Agent panel.
// Data: manager.proposalJson (items with group, current, proposed, options,
// unit, type, reason, warning/error; see docs/plan-card.md). Buttons call
// manager.answer(action, overridesJson, skipJson, text). Designers restyle
// this file; the manager calls are the contract.
import QtQuick 2.7
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4

Rectangle {
    id: base
    property var topbar: null
    property var panel: null
    property var proposal: JSON.parse(manager.proposalJson)
    property bool explain: false   // the "?" button: reasons and current values under each row

    readonly property color blue: "#1E6FD9"
    readonly property color blueLine: "#BBD7F5"
    readonly property color ink: "#16212C"
    readonly property color muted: "#8A94A3"

    width: 470
    x: (panel && panel.visible ? panel.x : (parent ? parent.width : 800)) - width - 16
    y: topbar ? topbar.y + topbar.height + 12 : 12
    height: Math.min(column.implicitHeight + 2, (parent ? parent.height : 800) - y - 24)
    z: 60
    visible: false
    radius: 14
    color: "white"
    border.color: blueLine
    onProposalChanged: explain = false

    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: wheel.accepted = false }

    function num(v) {
        if (typeof v !== "number") return v;
        var s = Math.round(v) === v ? String(v) : v.toFixed(2);
        return s.replace(".", ",");
    }
    function fmt(item, v) {
        if (v === null || v === undefined || v === "") return "—";
        var o = item.options || [];
        for (var i = 0; i < o.length; i++) if (String(o[i].value) === String(v)) return o[i].label;
        if (v === true) return "да";
        if (v === false) return "нет";
        return num(v) + (item.unit ? " " + item.unit : "");
    }
    function apply() {
        var overrides = {};
        for (var i = 0; i < rows.count; i++) {
            var row = rows.itemAt(i);
            var v = row.edited();
            if (v !== undefined && String(v) !== String(row.item.proposed)) overrides[row.item.id] = v;
        }
        manager.answer("apply", JSON.stringify(overrides), "[]", "");
    }

    Flickable {
        anchors.fill: parent
        anchors.margins: 1
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: column
            width: parent.width

            Item {  // header: the "?" toggles explanations
                width: parent.width
                height: 46
                Text { x: 20; anchors.verticalCenter: parent.verticalCenter; text: "Предлагаемые изменения"; color: base.ink; font.pixelSize: 13; font.bold: true }
                Rectangle {
                    anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
                    width: 24; height: 24; radius: 12
                    color: base.explain ? base.blue : "transparent"
                    border.color: base.explain ? base.blue : "#5A6878"; border.width: 1.5
                    Text { anchors.centerIn: parent; text: "?"; color: base.explain ? "white" : "#5A6878"; font.pixelSize: 13; font.bold: true }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: base.explain = !base.explain }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: base.blueLine }
            }

            Item { width: 1; height: 12 }

            Row {  // column titles
                x: 20
                spacing: 0
                Text { width: 170; text: "ПАРАМЕТР"; color: base.muted; font.pixelSize: 10 }
                Text { width: 120; text: "РЕКОМЕНДАЦИЯ"; color: base.muted; font.pixelSize: 10 }
                Text { text: "ВАШЕ ЗНАЧЕНИЕ"; color: base.muted; font.pixelSize: 10 }
            }

            Item { width: 1; height: 6 }

            Repeater {
                id: rows
                model: base.proposal.items

                delegate: Column {
                    id: row
                    property var item: modelData
                    property bool firstInGroup: index === 0 || base.proposal.items[index - 1].group !== item.group
                    property bool hasOptions: !!(item.options && item.options.length) || item.type === "bool"
                    property var choices: item.options && item.options.length ? item.options
                                         : [{ value: true, label: "да" }, { value: false, label: "нет" }]
                    property bool userChanged: false
                    function edited() {
                        if (hasOptions) return choices[combo.currentIndex].value;
                        var t = field.text.replace(",", ".").trim();
                        if (item.type === "int") return parseInt(t, 10);
                        if (item.type === "float") return parseFloat(t);
                        return field.text;
                    }
                    function step(sign) {
                        var v = parseFloat(field.text.replace(",", ".")) || 0;
                        var d = item.type === "int" ? 1 : (Math.abs(v) < 1 ? 0.01 : (Math.abs(v) < 10 ? 0.1 : 1));
                        v = Math.round((v + sign * d) * 100) / 100;
                        if (item.minimum !== undefined && v < item.minimum) v = item.minimum;
                        if (item.maximum !== undefined && v > item.maximum) v = item.maximum;
                        field.text = base.num(v);
                    }
                    width: column.width

                    Rectangle {  // section title
                        visible: row.firstInGroup
                        x: 16; width: parent.width - 32; height: visible ? 26 : 0
                        color: "#F4F6F9"
                        Text { x: 6; anchors.verticalCenter: parent.verticalCenter; text: (row.item.group || "").toUpperCase(); color: "#5A6878"; font.pixelSize: 10; font.bold: true }
                    }

                    Item {
                        width: parent.width
                        height: 38
                        readonly property bool differs: !row.item.unchanged

                        Text {
                            x: 22; width: 160
                            anchors.verticalCenter: parent.verticalCenter
                            text: row.item.label
                            elide: Text.ElideRight
                            color: row.item.error ? "#B42318" : base.ink
                            font.pixelSize: 12
                            font.bold: row.userChanged
                        }
                        Text {
                            x: 190; width: 112
                            anchors.verticalCenter: parent.verticalCenter
                            text: base.fmt(row.item, row.item.proposed)
                            elide: Text.ElideRight
                            color: parent.differs ? base.blue : base.muted
                            font.pixelSize: 12
                        }

                        Rectangle {  // "your value": number field with unit and arrows, or a list
                            x: 306; width: parent.width - x - 16; height: 30
                            anchors.verticalCenter: parent.verticalCenter
                            radius: 6
                            border.color: parent.differs || row.userChanged ? base.blueLine : "#E3E8EF"

                            TextInput {
                                id: field
                                visible: !row.hasOptions
                                x: 10; width: parent.width - 70
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.item.proposed === null || row.item.proposed === undefined ? "" : base.num(row.item.proposed)
                                color: parent.parent.differs || row.userChanged ? base.blue : base.ink
                                font.pixelSize: 13
                                selectByMouse: true
                                clip: true
                                onTextChanged: row.userChanged = String(row.edited()) !== String(row.item.proposed)
                            }
                            Text {
                                visible: !row.hasOptions
                                anchors.right: arrows.left; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter
                                text: row.item.unit || ""
                                color: base.muted
                                font.pixelSize: 11
                            }
                            Column {
                                id: arrows
                                visible: !row.hasOptions && (row.item.type === "int" || row.item.type === "float")
                                anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter
                                Text { text: "▴"; color: "#5A6878"; font.pixelSize: 10; MouseArea { anchors.fill: parent; anchors.margins: -2; onClicked: row.step(1) } }
                                Text { text: "▾"; color: "#5A6878"; font.pixelSize: 10; MouseArea { anchors.fill: parent; anchors.margins: -2; onClicked: row.step(-1) } }
                            }

                            ComboBox {
                                id: combo
                                visible: row.hasOptions
                                anchors.fill: parent
                                model: row.choices.map(function (o) { return o.label })
                                currentIndex: {
                                    for (var i = 0; i < row.choices.length; i++)
                                        if (String(row.choices[i].value) === String(row.item.proposed)) return i;
                                    return 0;
                                }
                                onActivated: row.userChanged = String(row.choices[index].value) !== String(row.item.proposed)
                                style: ComboBoxStyle {
                                    background: Rectangle { radius: 6; color: "white"; border.color: "transparent" }
                                    label: Text {
                                        text: control.currentText
                                        color: (!row.item.unchanged || row.userChanged) ? base.blue : base.ink
                                        font.pixelSize: 13
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }

                    Text {  // explanation: why, what it was, warnings
                        visible: base.explain || !!row.item.error || !!row.item.warning
                        x: 22; width: parent.width - 44
                        height: visible ? implicitHeight + 6 : 0
                        wrapMode: Text.Wrap
                        font.pixelSize: 11
                        color: row.item.error ? "#B42318" : (row.item.warning && !base.explain ? "#9A6A00" : base.muted)
                        text: {
                            var parts = [];
                            if (base.explain && row.item.reason) parts.push(row.item.reason);
                            if (base.explain && !row.item.unchanged) parts.push("было: " + base.fmt(row.item, row.item.current));
                            if (row.item.error) parts.push(row.item.error);
                            else if (row.item.warning) parts.push(row.item.warning);
                            return parts.join(" · ");
                        }
                    }
                }
            }

            Item { width: 1; height: 16 }

            Item {  // footer
                width: parent.width
                height: 52
                Text {
                    x: 22; width: 160
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Вопрос или правку можно написать в чат"
                    wrapMode: Text.Wrap
                    color: base.muted
                    font.pixelSize: 10
                }
                Text {
                    anchors.right: applyButton.left; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
                    text: "Отклонить"
                    color: "#5A6878"
                    font.pixelSize: 12
                    MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: manager.answer("reject", "{}", "[]", "") }
                }
                Rectangle {
                    id: applyButton
                    anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
                    width: 190; height: 44; radius: 10
                    color: applyArea.pressed ? "#1659B0" : base.blue
                    Text { anchors.centerIn: parent; text: "Применить изменения"; color: "white"; font.pixelSize: 14; font.bold: true }
                    MouseArea { id: applyArea; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: base.apply() }
                }
            }
            Item { width: 1; height: 12 }
        }
    }
}
