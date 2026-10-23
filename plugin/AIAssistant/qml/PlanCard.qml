// The plan card inside the slicer window. Data comes from manager.proposalJson
// (same items as the chat card, see docs/plan-card.md); the buttons call
// manager.answer(action, overridesJson, skipJson, text). Designers restyle this
// file; the ids and manager calls are the contract.
import QtQuick 2.2
import QtQuick.Controls 1.4
import QtQuick.Layouts 1.1
import QtQuick.Window 2.2

Window {
    id: base
    title: "AI Assistant — предлагаемые изменения"
    width: 640
    height: Math.min(720, content.implicitHeight + 32)
    minimumWidth: 480
    minimumHeight: 240
    flags: Qt.Dialog
    modality: Qt.NonModal
    color: palette.window

    SystemPalette { id: palette }

    property var proposal: JSON.parse(manager.proposalJson)
    property bool editing: false
    property bool asking: false
    onProposalChanged: { editing = false; asking = false; askText.text = "" }

    function fmt(item, value) {
        if (value === null || value === undefined || value === "") return "—";
        var options = item.options || [];
        for (var i = 0; i < options.length; i++)
            if (String(options[i].value) === String(value)) return options[i].label;
        if (value === true) return "да";
        if (value === false) return "нет";
        return String(value) + (item.unit ? " " + item.unit : "");
    }

    function apply() {
        var overrides = {}, skip = [];
        if (editing) {
            for (var i = 0; i < rows.count; i++) {
                var row = rows.itemAt(i);
                if (!row.used) { skip.push(row.item.id); continue; }
                var v = row.edited();
                if (String(v) !== String(row.item.proposed)) overrides[row.item.id] = v;
            }
        }
        manager.answer("apply", JSON.stringify(overrides), JSON.stringify(skip), "");
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: 16
        horizontalScrollBarPolicy: Qt.ScrollBarAlwaysOff

        ColumnLayout {
            id: content
            width: base.width - 48
            spacing: 10

            Label { text: "Ассистент предлагает изменения"; font.bold: true; font.pointSize: 13 }
            Label {
                text: "Примените, поправьте значения или уточните задачу"
                color: palette.dark
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }

            Repeater {
                id: rows
                model: base.proposal.items

                delegate: ColumnLayout {
                    id: row
                    property var item: modelData
                    property bool used: useBox.checked
                    property bool hasOptions: !!(item.options && item.options.length)
                    function edited() {
                        if (hasOptions) return item.options[combo.currentIndex].value;
                        if (item.type === "bool") return boolBox.checked;
                        if (item.type === "int") return parseInt(field.text, 10);
                        if (item.type === "float") return parseFloat(String(field.text).replace(",", "."));
                        return field.text;
                    }
                    Layout.fillWidth: true
                    spacing: 2
                    opacity: item.unchanged ? 0.6 : 1

                    Rectangle { Layout.fillWidth: true; height: 1; color: palette.dark; opacity: 0.4 }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        CheckBox { id: useBox; checked: true; visible: base.editing }
                        Label { text: row.item.label; font.bold: true }
                        Label { text: row.item.key && row.item.key !== row.item.label ? row.item.key : ""; color: palette.dark; font.family: "monospace" }
                        Item { Layout.fillWidth: true }
                        Label { text: base.fmt(row.item, row.item.current); font.strikeout: true; color: palette.dark; visible: !row.item.unchanged }
                        Label { text: "→"; visible: !row.item.unchanged }
                        Label {
                            text: base.fmt(row.item, row.item.proposed) + (row.item.unchanged ? " (без изменений)" : "")
                            font.bold: true
                            visible: !base.editing
                        }
                        ComboBox {
                            id: combo
                            visible: base.editing && row.hasOptions
                            model: row.hasOptions ? row.item.options.map(function (o) { return o.label }) : []
                            currentIndex: {
                                var o = row.item.options || [];
                                for (var i = 0; i < o.length; i++) if (String(o[i].value) === String(row.item.proposed)) return i;
                                return 0;
                            }
                            Layout.preferredWidth: 220
                        }
                        CheckBox { id: boolBox; visible: base.editing && !row.hasOptions && row.item.type === "bool"; checked: row.item.proposed === true }
                        TextField {
                            id: field
                            visible: base.editing && !row.hasOptions && row.item.type !== "bool"
                            text: row.item.proposed === null || row.item.proposed === undefined ? "" : String(row.item.proposed)
                            Layout.preferredWidth: 120
                        }
                    }
                    Label {
                        text: row.item.reason || ""
                        visible: !!row.item.reason
                        color: palette.dark
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        text: row.item.error || row.item.warning || ""
                        visible: !!(row.item.error || row.item.warning)
                        color: row.item.error ? "#b42318" : "#9a6a00"
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                }
            }

            RowLayout {
                spacing: 8
                Button { id: applyButton; text: "Применить"; isDefault: true; onClicked: base.apply() }
                Button { text: base.editing ? "Как предложено" : "Изменить"; onClicked: base.editing = !base.editing }
                Button { text: "Уточнить"; onClicked: { base.asking = !base.asking; if (base.asking) askText.forceActiveFocus() } }
                Button { text: "Отклонить"; onClicked: manager.answer("reject", "{}", "[]", askText.text) }
                Item { Layout.fillWidth: true }
                Button { text: "Открыть чат"; onClicked: manager.openChat() }
            }

            RowLayout {
                visible: base.asking
                Layout.fillWidth: true
                TextArea {
                    id: askText
                    Layout.fillWidth: true
                    Layout.preferredHeight: 60
                }
                Button {
                    text: "Отправить"
                    enabled: askText.text.trim().length > 0
                    onClicked: manager.answer("revise", "{}", "[]", askText.text.trim())
                }
            }
        }
    }
}
