// "База знаний": the materials table (knowledge/materials.csv) as a list and a form,
// and the rule files (knowledge/*.md) as text. A window of its own, like "Память агента";
// AgentPanel.qml holds it and opens it on knowledge.windowRequested. `knowledge` is
// knowledge_ui.KnowledgeBridge: every call returns JSON.
// Qt 5.10: QtQuick 2.7, Controls 1.4 (as the rest of the plugin), no TableView.
import QtQuick 2.7
import QtQuick.Controls 1.4
import QtQuick.Window 2.2

Window {
    id: win
    property var knowledge: null
    title: "База знаний"
    width: 1000; height: 680
    minimumWidth: 860; minimumHeight: 480
    flags: Qt.Dialog
    color: "white"

    property string tab: "materials"        // materials | rules
    // materials
    property string query: ""
    property var items: []
    property int total: 0                   // materials in the table, whatever the search
    property string selected: ""            // material_id in the form ("" = none or new)
    property bool isNew: false
    property var vals: ({})                 // the form's values, edited in place (see setVal)
    property var groups: []                 // [{ title, cols: [column] }] for the form
    property var errors: ({})
    property bool formDirty: false
    property int formVersion: 0             // bumps when another material is loaded: the fields reset
    property string confirmDelete: ""
    // rules
    property var files: []
    property string file: ""
    property string fileText: ""            // as saved: the editor compares against it
    readonly property bool textDirty: tab === "rules" && file !== "" && editor.text !== fileText
    readonly property bool dirty: (tab === "materials" && formDirty) || textDirty
    // footer
    property var ask: null                  // { text, next } before leaving unsaved changes
    property string toast: ""
    property bool toastOk: true
    Style { id: st }

    function call(name, a, b) {
        var r = JSON.parse(b !== undefined ? knowledge[name](a, b) : a !== undefined ? knowledge[name](a) : knowledge[name]());
        if (r && r.error) { say(r.error, false); return null; }
        return r;
    }
    function open() {
        ask = null; confirmDelete = "";
        reloadList();
        files = call("files") || [];
        if (tab === "rules" && file === "" && files.length) loadFile(files[0].name);
        show(); raise(); requestActivate();
    }
    function say(text, ok) { toast = text; toastOk = ok; toastTimer.restart(); }

    // anything that would drop unsaved edits goes through here
    function leave(next) {
        if (!dirty) { next(); return; }
        ask = { text: tab === "rules" ? "В файле правил есть несохранённые изменения." : "В материале есть несохранённые изменения.", next: next };
    }
    function saveCurrent() { return tab === "rules" ? saveFile() : saveMaterial(); }

    // ---- materials
    function reloadList() {
        items = knowledge ? (call("materials", query) || []) : [];
        total = query === "" ? items.length : knowledge ? (call("materials", "") || []).length : 0;
    }
    function showForm(data, id, fresh) {
        if (!data) return;
        var byGroup = {}, order = [];
        for (var i = 0; i < data.columns.length; i++) {
            var c = data.columns[i];
            if (!byGroup[c.group]) { byGroup[c.group] = []; order.push(c.group); }
            byGroup[c.group].push(c);
        }
        var known = ["Основное", "Стойкость", "Механика", "Печать", "Примечания", "Прочее"];
        order.sort(function (a, b) { return known.indexOf(a) - known.indexOf(b); });
        var out = [];
        for (var j = 0; j < order.length; j++) out.push({ title: order[j], cols: byGroup[order[j]] });
        vals = data.values; errors = {}; formDirty = fresh; isNew = fresh;
        selected = id; groups = out; formVersion++;
        form.contentY = 0;
    }
    function selectMaterial(id) { leave(function () { confirmDelete = ""; showForm(call("material", id), id, false); }); }
    function newMaterial() { leave(function () { showForm(call("newMaterial"), "", true); }); }
    function duplicate() {
        var from = selected;
        leave(function () { showForm(call("duplicateMaterial", from), "", true); });
    }
    function setVal(key, value) {
        if (String(vals[key]) === String(value)) return;
        vals[key] = value;
        formDirty = true;
        if (errors[key]) { var e = JSON.parse(JSON.stringify(errors)); delete e[key]; errors = e; }
    }
    function saveMaterial() {
        var r = call("saveMaterial", isNew ? "" : selected, JSON.stringify(vals));
        if (!r) return false;
        if (!r.ok) { errors = r.errors; say("Не сохранено: исправьте поля, отмеченные красным", false); return false; }
        formDirty = false; isNew = false; selected = r.material_id;
        reloadList();
        say("Сохранено. Агент учтёт материал со следующего шага.", true);
        return true;
    }
    function revertMaterial() {
        if (isNew) { selected = ""; groups = []; formDirty = false; isNew = false; return; }
        showForm(call("material", selected), selected, false);
    }
    function deleteMaterial(id) {
        confirmDelete = "";
        if (!call("deleteMaterial", id)) return;
        if (selected === id) { selected = ""; groups = []; formDirty = false; isNew = false; }
        reloadList();
        say("Материал удалён. Прежняя таблица сохранена рядом как materials.csv.bak.", true);
    }

    // ---- rule files
    function loadFile(name) {
        var r = call("readFile", name);
        if (!r) return;
        file = name; fileText = r.text; editor.text = r.text;
        editor.cursorPosition = 0;
    }
    function selectFile(name) { if (name !== file) leave(function () { loadFile(name); }); }
    function saveFile() {
        var text = editor.text;
        if (!call("saveFile", file, text)) return false;
        fileText = text;
        files = call("files") || files;
        say("Сохранено. Правила войдут в работу агента с нового диалога.", true);
        return true;
    }
    function setTab(name) {
        if (tab === name) return;
        leave(function () {
            formDirty = false; editor.text = fileText;
            tab = name; toast = "";
            if (name === "rules" && file === "" && files.length) loadFile(files[0].name);
        });
    }

    onClosing: {
        if (dirty) { close.accepted = false; leave(function () { formDirty = false; editor.text = fileText; win.hide(); }); }
    }
    Timer { id: toastTimer; interval: 7000; onTriggered: win.toast = "" }

    // ---- header
    Item {
        id: header
        width: parent.width; height: 64
        Text {
            x: 24; anchors.verticalCenter: parent.verticalCenter
            text: "База знаний"
            color: st.textPrimary
            font.family: st.family; font.pixelSize: 18; font.weight: Font.DemiBold
        }
        ActionButton {
            anchors.right: parent.right; anchors.rightMargin: 24
            anchors.verticalCenter: parent.verticalCenter
            label: "Открыть папку"; kind: "secondary"
            onClicked: win.knowledge.openFolder()
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: st.border }
    }

    // ---- tabs
    Row {
        id: tabs
        x: 24; y: header.height
        height: 44
        spacing: 24
        Repeater {
            model: [{ name: "materials", label: "Материалы", count: win.total }, { name: "rules", label: "Правила", count: win.files.length }]
            delegate: Item {
                width: tabLabel.implicitWidth + tabCount.implicitWidth + 6; height: tabs.height
                readonly property bool on: win.tab === modelData.name
                Text {
                    id: tabLabel
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.label
                    color: parent.on ? st.accent : st.textPrimary
                    font.family: st.family; font.pixelSize: 14; font.weight: parent.on ? Font.DemiBold : Font.Normal
                }
                Text {
                    id: tabCount
                    x: tabLabel.implicitWidth + 6
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.count
                    color: st.textSecondary
                    font.family: st.family; font.pixelSize: 12
                }
                Rectangle { visible: parent.on; anchors.bottom: parent.bottom; width: parent.width; height: 2; color: st.accent }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: win.setTab(modelData.name) }
            }
        }
    }
    Rectangle { y: tabs.y + tabs.height; width: parent.width; height: 1; color: st.border }

    // ================================================================ materials
    Item {
        id: materialsTab
        visible: win.tab === "materials"
        x: 24; y: tabs.y + tabs.height + 16
        width: parent.width - 48
        height: parent.height - y - footer.height - 8

        // ---- the list
        Item {
            id: listPane
            width: 330; height: parent.height
            Rectangle {
                id: search
                width: parent.width - addButton.width - 8; height: 30; radius: 6
                border.width: 1; border.color: searchInput.activeFocus ? st.borderAccent : st.border
                TextInput {
                    id: searchInput
                    x: 10; width: parent.width - 20
                    anchors.verticalCenter: parent.verticalCenter
                    color: st.textPrimary
                    font.family: st.family; font.pixelSize: 12
                    selectByMouse: true; clip: true
                    onTextChanged: { win.query = text; win.reloadList(); }
                }
                Text {
                    visible: searchInput.text === ""
                    x: 10; anchors.verticalCenter: parent.verticalCenter
                    text: "Название, основа, применение"
                    color: st.iconMuted
                    font.family: st.family; font.pixelSize: 12
                }
            }
            ActionButton {
                id: addButton
                anchors.right: parent.right
                label: "+ Материал"; kind: "secondary"
                onClicked: win.newMaterial()
            }
            Rectangle {
                y: search.height + 10
                width: parent.width; height: parent.height - y
                radius: 8; border.width: 1; border.color: st.border
                clip: true
                ListView {
                    id: materialList
                    anchors.fill: parent; anchors.margins: 1
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: win.items
                    delegate: Item {
                        id: mrow
                        width: materialList.width; height: 52
                        readonly property bool current: !win.isNew && win.selected === modelData.material_id
                        readonly property bool asking: win.confirmDelete === modelData.material_id
                        Rectangle { anchors.fill: parent; color: mrow.current ? st.accentSoft : rowHover.containsMouse ? st.groupBg : "white" }
                        MouseArea { id: rowHover; anchors.fill: parent; hoverEnabled: true; onClicked: win.selectMaterial(modelData.material_id) }
                        Column {
                            x: 12; anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - 12 - (mrow.asking ? 120 : 70)
                            spacing: 3
                            Text {
                                width: parent.width; elide: Text.ElideRight
                                text: modelData.name || modelData.material_id
                                color: st.textPrimary
                                font.family: st.family; font.pixelSize: 13; font.weight: Font.Medium
                            }
                            Text {
                                width: parent.width; elide: Text.ElideRight
                                text: [modelData.base, modelData.good_for].filter(function (s) { return !!s; }).join(" · ")
                                color: st.textSecondary
                                font.family: st.family; font.pixelSize: 11
                            }
                        }
                        Icon {
                            visible: !mrow.asking
                            anchors.right: parent.right; anchors.rightMargin: 46
                            anchors.verticalCenter: parent.verticalCenter
                            width: 16; height: 16; name: "pencil"; opacity: 0.75
                            MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: win.selectMaterial(modelData.material_id) }
                        }
                        Icon {
                            visible: !mrow.asking
                            anchors.right: parent.right; anchors.rightMargin: 14
                            anchors.verticalCenter: parent.verticalCenter
                            width: 16; height: 16; name: "trash"
                            MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: win.confirmDelete = modelData.material_id }
                        }
                        Row {
                            visible: mrow.asking
                            anchors.right: parent.right; anchors.rightMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 12
                            Text { text: "Удалить?"; color: st.textPrimary; font.family: st.family; font.pixelSize: 12 }
                            Text {
                                text: "Да"; color: st.danger
                                font.family: st.family; font.pixelSize: 12; font.weight: Font.DemiBold
                                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: win.deleteMaterial(modelData.material_id) }
                            }
                            Text {
                                text: "Нет"; color: st.accent
                                font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: win.confirmDelete = "" }
                            }
                        }
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: st.border }
                    }
                    Text {
                        visible: materialList.count === 0
                        anchors.centerIn: parent
                        text: win.query ? "Ничего не нашлось" : "Таблица материалов пуста"
                        color: st.textSecondary
                        font.family: st.family; font.pixelSize: 12
                    }
                }
            }
        }

        // ---- the form
        Rectangle {
            id: formPane
            x: listPane.width + 16
            width: parent.width - x; height: parent.height
            radius: 8; border.width: 1; border.color: st.border
            clip: true

            Text {
                visible: win.groups.length === 0
                anchors.centerIn: parent
                width: parent.width - 80
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                text: "Выберите материал слева или добавьте новый. Агент подбирает материал по этой таблице: свойства, стойкость, ограничения."
                color: st.textSecondary
                font.family: st.family; font.pixelSize: 13
            }

            Item {
                id: formHeader
                visible: win.groups.length > 0
                width: parent.width; height: 52
                Text {
                    x: 16; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 32 - formButtons.width
                    elide: Text.ElideRight
                    text: win.isNew ? "Новый материал" : (win.vals.name || win.selected) + (win.formDirty ? " •" : "")
                    color: st.textPrimary
                    font.family: st.family; font.pixelSize: 15; font.weight: Font.DemiBold
                }
                Row {
                    id: formButtons
                    anchors.right: parent.right; anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    ActionButton { visible: !win.isNew; label: "Дублировать"; kind: "tertiary"; onClicked: win.duplicate() }
                    ActionButton { visible: win.formDirty; label: "Отменить"; kind: "secondary"; onClicked: win.revertMaterial() }
                    ActionButton { label: "Сохранить"; opacity: win.formDirty ? 1 : 0.5; onClicked: if (win.formDirty) win.saveMaterial() }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: st.border }
            }

            Flickable {
                id: form
                visible: win.groups.length > 0
                y: formHeader.height
                width: parent.width; height: parent.height - y - 1
                contentHeight: formColumn.implicitHeight + 24
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                Column {
                    id: formColumn
                    x: 16; y: 12
                    width: form.width - 32
                    spacing: 14
                    Repeater {
                        model: win.groups
                        delegate: Column {
                            width: formColumn.width
                            spacing: 8
                            Text {
                                text: modelData.title.toUpperCase()
                                color: st.textSecondary
                                font.family: st.family; font.pixelSize: 10; font.weight: Font.DemiBold
                            }
                            Flow {
                                width: parent.width
                                spacing: 12
                                Repeater {
                                    model: modelData.cols
                                    delegate: KnowledgeField {
                                        col: modelData
                                        version: win.formVersion
                                        width: col.kind === "text" && (col.key === "good_for" || col.key === "caveats")
                                               ? formColumn.width : (formColumn.width - 12) / 2
                                        value: String(win.vals[col.key] === undefined ? "" : win.vals[col.key])
                                        error: win.errors[col.key] || ""
                                        onEdited: win.setVal(col.key, value)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ================================================================ rule files
    Item {
        id: rulesTab
        visible: win.tab === "rules"
        x: 24; y: tabs.y + tabs.height + 16
        width: parent.width - 48
        height: parent.height - y - footer.height - 8

        Rectangle {
            id: fileList
            width: 260; height: parent.height
            radius: 8; border.width: 1; border.color: st.border
            clip: true
            Column {
                x: 1; y: 1; width: parent.width - 2
                Repeater {
                    model: win.files
                    delegate: Item {
                        width: parent.width; height: 54
                        readonly property bool current: win.file === modelData.name
                        Rectangle { anchors.fill: parent; color: parent.current ? st.accentSoft : fileHover.containsMouse ? st.groupBg : "white" }
                        Column {
                            x: 12; anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - 24; spacing: 3
                            Text {
                                width: parent.width; elide: Text.ElideRight
                                text: modelData.title + (parent.parent.current && win.textDirty ? " •" : "")
                                color: st.textPrimary
                                font.family: st.family; font.pixelSize: 13; font.weight: Font.Medium
                            }
                            Text {
                                text: modelData.name
                                color: st.textSecondary
                                font.family: st.family; font.pixelSize: 11
                            }
                        }
                        MouseArea { id: fileHover; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: win.selectFile(modelData.name) }
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: st.border }
                    }
                }
            }
        }

        Item {
            x: fileList.width + 16
            width: parent.width - x; height: parent.height
            Row {
                id: fileButtons
                anchors.right: parent.right
                spacing: 8
                ActionButton { visible: win.textDirty; label: "Отменить изменения"; kind: "secondary"; onClicked: editor.text = win.fileText }
                ActionButton { label: "Сохранить"; opacity: win.textDirty ? 1 : 0.5; onClicked: if (win.textDirty) win.saveFile() }
            }
            Text {
                anchors.verticalCenter: fileButtons.verticalCenter
                width: parent.width - fileButtons.width - 12
                wrapMode: Text.Wrap
                text: "Markdown. Агент читает эти файлы целиком, правки действуют с нового диалога."
                color: st.textSecondary
                font.family: st.family; font.pixelSize: 11
            }
            TextArea {
                id: editor
                y: fileButtons.height + 10
                width: parent.width; height: parent.height - y
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                font.pixelSize: 12
            }
        }
    }

    // ---- footer: unsaved changes, or the result of the last action
    Item {
        id: footer
        anchors.bottom: parent.bottom
        width: parent.width
        height: 56

        Rectangle {
            visible: !!win.ask
            x: 24; width: parent.width - 48
            anchors.verticalCenter: parent.verticalCenter
            height: 44; radius: 8
            color: st.accentSoft
            Text {
                x: 14
                width: parent.width - 28 - askButtons.width - 16
                anchors.verticalCenter: parent.verticalCenter
                text: win.ask ? win.ask.text : ""
                elide: Text.ElideRight
                color: st.textPrimary
                font.family: st.family; font.pixelSize: 12
            }
            Row {
                id: askButtons
                anchors.right: parent.right; anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                ActionButton { label: "Отмена"; kind: "secondary"; onClicked: win.ask = null }
                ActionButton {
                    label: "Не сохранять"; kind: "tertiary"
                    onClicked: { var next = win.ask.next; win.ask = null; win.formDirty = false; editor.text = win.fileText; next(); }
                }
                ActionButton {
                    label: "Сохранить"
                    onClicked: { var next = win.ask.next; win.ask = null; if (win.saveCurrent()) next(); }
                }
            }
        }
        Row {
            visible: !win.ask
            x: 24
            anchors.verticalCenter: parent.verticalCenter
            spacing: 8
            Icon { visible: win.toast !== "" && win.toastOk; anchors.verticalCenter: parent.verticalCenter; name: "file_check"; width: 16; height: 16 }
            Icon { visible: win.toast !== "" && !win.toastOk; anchors.verticalCenter: parent.verticalCenter; name: "triangle_alert"; width: 16; height: 16 }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: win.toast !== "" ? win.toast
                      : "Перед каждой записью прежний файл сохраняется рядом с расширением .bak."
                color: win.toast === "" ? st.textSecondary : win.toastOk ? st.success : st.danger
                font.family: st.family; font.pixelSize: 12
            }
        }
        ActionButton {
            visible: !win.ask
            anchors.right: parent.right; anchors.rightMargin: 24
            anchors.verticalCenter: parent.verticalCenter
            label: "Закрыть"; kind: "secondary"
            onClicked: win.close()
        }
    }
}
