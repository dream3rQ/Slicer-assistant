// "Память агента": the rules the agent applies by itself and the journal of
// every edit made in the plan card, with rollback (plan v0.7.0, task 3).
// A window of its own, like the settings window; AgentPanel.qml holds it and
// opens it on memory.windowRequested. `memory` is memory_ui.MemoryBridge: every
// call returns JSON rows already worded for people (key_label, value_text, ...).
// Qt 5.10: QtQuick 2.7, Controls 1.4, no Controls 2.
import QtQuick 2.7
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4
import QtQuick.Window 2.2

Window {
    id: win
    property var memory: null
    title: "Память агента"
    width: 1000; height: 640
    minimumWidth: 900; minimumHeight: 460
    flags: Qt.Dialog
    color: "white"

    property string tab: "rules"            // rules | events
    property var ruleRows: []
    property var eventRows: []
    property int ruleTotal: 0
    property int eventTotal: 0
    property var facets: ({ materials: [], modes: [], keys: [] })
    property var flt: ({ period: "", material: "", mode: "", key: "", status: "" })
    property var sortRules: ({ by: "ts", desc: true })
    property var sortEvents: ({ by: "ts", desc: true })
    property string editing: ""             // id of the rule being edited
    property string editError: ""
    property string confirmDelete: ""       // id of the rule asking "Удалить?"
    property var ask: null                  // the bottom bar: { text, kind: "rollback"|"import", ts }
    property string toast: ""
    property bool toastOk: true
    readonly property bool expert: memory ? memory.role === "expert" : false
    readonly property bool filtered: flt.period !== "" || flt.material !== "" || flt.mode !== "" || flt.key !== "" || flt.status !== ""
    Style { id: st }

    function open() {
        editing = ""; confirmDelete = ""; ask = null;
        reload();
        show(); raise(); requestActivate();
    }
    function query() {
        var f = {};
        for (var k in flt) if (flt[k] !== "") f[k] = flt[k];
        if (f.status !== undefined && tab === "events") { f.action = f.status; delete f.status; }
        return f;
    }
    function reload() {
        if (!memory) return;
        facets = JSON.parse(memory.facets());
        var f = query();
        if (tab === "rules") {
            ruleRows = JSON.parse(memory.rules(JSON.stringify(f), JSON.stringify(sortRules)));
            eventTotal = JSON.parse(memory.events("{}", "{}")).length;
            ruleTotal = filtered ? JSON.parse(memory.rules("{}", "{}")).length : ruleRows.length;
        } else {
            eventRows = JSON.parse(memory.events(JSON.stringify(f), JSON.stringify(sortEvents)));
            ruleTotal = JSON.parse(memory.rules("{}", "{}")).length;
            eventTotal = filtered ? JSON.parse(memory.events("{}", "{}")).length : eventRows.length;
        }
    }
    function setFilter(key, value) {
        var f = JSON.parse(JSON.stringify(flt));
        f[key] = value;
        flt = f;
        reload();
    }
    function clearFilters() {
        flt = { period: "", material: "", mode: "", key: "", status: "" };
        reload();
    }
    function setTab(name) {
        if (tab === name) return;
        tab = name; editing = ""; confirmDelete = "";
        if (flt.status !== "") { var f = JSON.parse(JSON.stringify(flt)); f.status = ""; flt = f; }  // statuses differ per tab
        reload();
    }
    function sortBy(by) {
        var s = tab === "rules" ? sortRules : sortEvents;
        var next = { by: by, desc: s.by === by ? !s.desc : by === "ts" };
        if (tab === "rules") sortRules = next; else sortEvents = next;
        reload();
    }
    function say(text, ok) { toast = text; toastOk = ok; toastTimer.restart(); }
    function act(error, done) { if (error) say(error, false); else if (done) say(done, true); }

    // a typed value back to JSON: numbers with a comma or point, да/нет, else text
    function parseValue(text, like) {
        var t = String(text).trim();
        if (t === "да" || t === "true") return true;
        if (t === "нет" || t === "false") return false;
        var n = Number(t.replace(",", "."));
        if (t !== "" && !isNaN(n) && typeof like !== "string") return n;
        return t;
    }
    function rawText(v) {
        if (v === true) return "да";
        if (v === false) return "нет";
        return typeof v === "number" ? String(v).replace(".", ",") : String(v === null || v === undefined ? "" : v);
    }

    Connections {
        target: win.memory
        onChanged: if (win.visible) win.reload()
        onNotice: win.say(text, ok)
        onRoleChanged: win.editing = ""
    }
    Timer { id: toastTimer; interval: 7000; onTriggered: win.toast = "" }

    readonly property var ruleColumns: [
        { title: "Настройка", field: "key_label", sort: "key", flex: 1.5 },
        { title: "Значение", field: "value_text", w: 98, strong: true },
        { title: "Область", field: "scope_text", sort: "material", flex: 1.75 },
        { title: "Статус", field: "status_text", sort: "status", w: 107, kind: "status" },
        { title: "Кто создал", field: "role_text", sort: "role", w: 103 },
        { title: "Когда", field: "when", sort: "ts", w: 136 },
        { title: "", kind: "actions", w: 160 }
    ]
    readonly property var eventColumns: [
        { title: "Когда", field: "when", sort: "ts", w: 116 },
        { title: "Деталь", field: "part_text", sort: "part", flex: 1.1 },
        { title: "Материал", field: "material_text", sort: "material", flex: 1.1 },
        { title: "Режим", field: "mode_text", sort: "mode", w: 72 },
        { title: "Настройка", field: "key_label", sort: "key", flex: 1.4 },
        { title: "Агент → вы", field: "change", flex: 1.3, strong: true },
        { title: "Кто", field: "role_text", sort: "role", w: 76 },
        { title: "Что", field: "action_text", sort: "action", w: 138, kind: "action" },
        { title: "", kind: "rollback", w: 112 }
    ]
    readonly property var columns: tab === "rules" ? ruleColumns : eventColumns
    function colWidth(col, total) {
        var fixed = 0, flex = 0;
        for (var i = 0; i < columns.length; i++) {
            if (columns[i].flex) flex += columns[i].flex; else fixed += columns[i].w;
        }
        return col.flex ? Math.max(60, (total - fixed) * col.flex / flex) : col.w;
    }
    function statusColor(status) {
        return status === "active" ? st.success : status === "candidate" ? st.warning
             : status === "rejected" ? st.danger : st.textSecondary;
    }
    function statusBg(status) {
        return status === "active" ? "#E6F4EF" : status === "candidate" ? "#FFF6E0"
             : status === "rejected" ? st.dangerBg : st.groupBg;
    }

    // ---- header: title, role, import/export
    Item {
        id: header
        width: parent.width; height: 64
        Row {
            x: 24; anchors.verticalCenter: parent.verticalCenter
            spacing: 10
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Память агента"
                color: st.textPrimary
                font.family: st.family; font.pixelSize: 18; font.weight: Font.DemiBold
            }
        }
        Row {
            anchors.right: parent.right; anchors.rightMargin: 24
            anchors.verticalCenter: parent.verticalCenter
            spacing: 12
            Rectangle {  // Новичок / Эксперт, in the style of the 3D/5D pill
                id: rolePill
                anchors.verticalCenter: parent.verticalCenter
                width: 176; height: 28; radius: 14
                color: st.accentSoft
                Rectangle {
                    x: win.expert ? 88 : 2; y: 2
                    width: 86; height: 24; radius: 12
                    color: st.accent
                    Behavior on x { NumberAnimation { duration: 120 } }
                }
                Row {
                    x: 2; y: 2
                    Repeater {
                        model: [{ value: "novice", label: "Новичок" }, { value: "expert", label: "Эксперт" }]
                        delegate: Item {
                            width: 86; height: 24
                            readonly property bool on: (modelData.value === "expert") === win.expert
                            Text {
                                anchors.centerIn: parent
                                text: modelData.label
                                color: parent.on ? "white" : st.accent
                                font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: if (win.memory) win.memory.setRole(modelData.value) }
                        }
                    }
                }
            }
            Item { width: 4; height: 1 }
            ActionButton {
                anchors.verticalCenter: parent.verticalCenter
                label: "Импорт"; kind: "secondary"
                onClicked: win.ask = { kind: "import", text: "Как добавить правила из файла?" }
            }
            ActionButton {
                anchors.verticalCenter: parent.verticalCenter
                label: "Экспорт"; kind: "secondary"
                onClicked: win.memory.exportFile()
            }
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
            model: [{ name: "rules", label: "Правила", count: win.ruleTotal }, { name: "events", label: "Журнал правок", count: win.eventTotal }]
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

    // ---- filters
    Row {
        id: filters
        x: 24; y: tabs.y + tabs.height + 14
        spacing: 10
        MemoryFilter {
            title: "Период"; allLabel: "всё время"; width: 130
            value: win.flt.period
            options: [{ value: "day", label: "за сутки" }, { value: "week", label: "за неделю" }, { value: "month", label: "за 30 дней" }]
            onPicked: win.setFilter("period", value)
        }
        MemoryFilter {
            title: "Материал"; width: 180
            value: win.flt.material; options: win.facets.materials
            onPicked: win.setFilter("material", value)
        }
        MemoryFilter {
            title: "Режим"; width: 110
            value: win.flt.mode; options: win.facets.modes
            onPicked: win.setFilter("mode", value)
        }
        MemoryFilter {
            title: "Настройка"; width: 200
            value: win.flt.key; options: win.facets.keys
            onPicked: win.setFilter("key", value)
        }
        MemoryFilter {
            title: win.tab === "rules" ? "Статус" : "Что"; width: 150
            value: win.flt.status
            options: win.tab === "rules"
                ? [{ value: "active", label: "действует" }, { value: "candidate", label: "кандидат" }, { value: "disabled", label: "выключено" }]
                : [{ value: "edit", label: "правка" }, { value: "rule_add", label: "новое правило" }, { value: "rule_edit", label: "правило изменено" },
                   { value: "rule_off", label: "правило выключено" }, { value: "rule_delete", label: "правило удалено" }, { value: "rollback", label: "откат" }]
            onPicked: win.setFilter("status", value)
        }
        Text {
            visible: win.filtered
            anchors.bottom: parent.bottom; anchors.bottomMargin: 7
            text: "Сбросить"
            color: st.accent
            font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
            MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: win.clearFilters() }
        }
    }

    // ---- the table
    Rectangle {
        id: table
        x: 24; y: filters.y + filters.height + 16
        width: parent.width - 48
        height: parent.height - y - footer.height - 12
        radius: 8
        color: "white"
        border.width: 1; border.color: st.border
        clip: true

        Rectangle {  // column titles; a click sorts, a second click turns the order
            id: titles
            x: 1; y: 1
            width: parent.width - 2; height: 32
            radius: 7
            color: st.groupBg
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 8; color: st.groupBg }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: st.border }
            Row {
                x: 12
                height: parent.height
                Repeater {
                    model: win.columns
                    delegate: Item {
                        width: win.colWidth(modelData, titles.width - 24); height: titles.height
                        readonly property var sort: win.tab === "rules" ? win.sortRules : win.sortEvents
                        readonly property bool sorted: !!modelData.sort && sort.by === modelData.sort
                        Text {
                            id: colTitle
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.title.toUpperCase()
                            color: parent.sorted ? st.accent : st.textSecondary
                            font.family: st.family; font.pixelSize: 10; font.weight: parent.sorted ? Font.DemiBold : Font.Normal
                        }
                        MouseArea {
                            visible: !!modelData.sort
                            width: colTitle.implicitWidth + 16; height: parent.height
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.sortBy(modelData.sort)
                        }
                    }
                }
            }
        }

        ListView {
            id: list
            x: 1; y: titles.height + 1
            width: parent.width - 2; height: parent.height - titles.height - 2
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: win.tab === "rules" ? win.ruleRows : win.eventRows

            delegate: Column {
                id: line
                property var r: modelData
                readonly property bool isEditing: win.tab === "rules" && win.editing === r.id
                width: list.width

                Item {
                    width: parent.width; height: 40
                    Rectangle { anchors.fill: parent; color: line.isEditing ? st.accentSoft : hover.containsMouse ? st.groupBg : "white" }
                    MouseArea { id: hover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
                    Row {
                        x: 12
                        height: parent.height
                        Repeater {
                            model: win.columns
                            delegate: Item {
                                id: cell
                                property var col: modelData
                                width: win.colWidth(col, list.width - 24); height: parent.height

                                Text {  // plain text cells
                                    visible: !cell.col.kind || cell.col.kind === "action"
                                    width: parent.width - 10
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: cell.col.field ? (line.r[cell.col.field] === undefined || line.r[cell.col.field] === null || line.r[cell.col.field] === "" ? "—" : String(line.r[cell.col.field])) : ""
                                    elide: Text.ElideRight
                                    color: cell.col.kind === "action" && line.r.action !== "edit" ? st.accent
                                         : cell.col.strong ? st.textPrimary : (cell.col.field === "when" ? st.textSecondary : st.textPrimary)
                                    font.family: st.family; font.pixelSize: 12
                                    font.weight: cell.col.strong ? Font.Medium : Font.Normal
                                }
                                Rectangle {  // rule status as a pill
                                    visible: cell.col.kind === "status"
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: statusText.implicitWidth + 16; height: 22; radius: 11
                                    color: win.statusBg(line.r.status)
                                    Text {
                                        id: statusText
                                        anchors.centerIn: parent
                                        text: line.r.status_text || ""
                                        color: win.statusColor(line.r.status)
                                        font.family: st.family; font.pixelSize: 11; font.weight: Font.Medium
                                    }
                                }
                                Item {  // rule actions: on/off switch, edit, delete (as in the mock-up)
                                    visible: cell.col.kind === "actions"
                                    width: parent.width; height: parent.height
                                    readonly property bool asking: win.confirmDelete === line.r.id
                                    Rectangle {  // the switch: on = the rule is used (active or a candidate)
                                        id: toggle
                                        visible: !parent.asking
                                        x: 0; anchors.verticalCenter: parent.verticalCenter
                                        width: 32; height: 18; radius: 9
                                        readonly property bool on: line.r.status !== "disabled"
                                        color: on ? st.accentSoft : st.tertiaryBg
                                        border.width: 1; border.color: on ? st.borderAccent : st.border
                                        Rectangle {
                                            x: toggle.on ? toggle.width - width - 2 : 2; y: 2
                                            width: 14; height: 14; radius: 7
                                            color: toggle.on ? st.accent : "white"
                                            border.width: toggle.on ? 0 : 1; border.color: st.border
                                        }
                                        MouseArea {
                                            anchors.fill: parent; anchors.margins: -6
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: win.act(win.memory.setRuleEnabled(line.r.id, !toggle.on),
                                                               toggle.on ? "Правило выключено" : "Правило включено")
                                        }
                                    }
                                    Icon {
                                        visible: !parent.asking
                                        x: 66; anchors.verticalCenter: parent.verticalCenter
                                        width: 16; height: 16
                                        name: "pencil"
                                        opacity: line.isEditing ? 1 : editArea.containsMouse ? 0.85 : 0.7
                                        MouseArea {
                                            id: editArea
                                            anchors.fill: parent; anchors.margins: -8
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: { win.editError = ""; win.editing = win.editing === line.r.id ? "" : line.r.id; win.confirmDelete = "" }
                                        }
                                    }
                                    Icon {
                                        visible: !parent.asking
                                        x: 118; anchors.verticalCenter: parent.verticalCenter
                                        width: 16; height: 16
                                        name: "trash"
                                        MouseArea {
                                            anchors.fill: parent; anchors.margins: -8
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: { win.confirmDelete = line.r.id; win.editing = "" }
                                        }
                                    }
                                    Row {  // "Удалить? Да Нет" in place of the icons
                                        visible: parent.asking
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 12
                                        Text { text: "Удалить?"; color: st.textPrimary; font.family: st.family; font.pixelSize: 12 }
                                        Text {
                                            text: "Да"; color: st.danger
                                            font.family: st.family; font.pixelSize: 12; font.weight: Font.DemiBold
                                            MouseArea {
                                                anchors.fill: parent; anchors.margins: -6
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: { var id = line.r.id; win.confirmDelete = "";
                                                             win.act(win.memory.deleteRule(id), "Правило удалено. Вернуть можно откатом в журнале.") }
                                            }
                                        }
                                        Text {
                                            text: "Нет"; color: st.accent
                                            font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                                            MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: win.confirmDelete = "" }
                                        }
                                    }
                                }
                                Text {  // journal: roll the rules back to right after this line
                                    visible: cell.col.kind === "rollback"
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "Откатить сюда"
                                    color: st.accent
                                    opacity: hover.containsMouse || rollArea.containsMouse ? 1 : 0.55
                                    font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                                    MouseArea {
                                        id: rollArea
                                        anchors.fill: parent; anchors.margins: -4
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: win.ask = { kind: "rollback", ts: line.r.ts,
                                            text: "Вернуть правила к состоянию на " + line.r.when + "? Правила, созданные позже, пропадут, "
                                                + "изменённые вернут прежние значения. Журнал сохранится, откат тоже запишется в него." }
                                    }
                                }
                            }
                        }
                    }
                }

                Item {  // editing a rule: value, scope, note
                    id: editor
                    visible: line.isEditing
                    width: parent.width
                    height: visible ? editColumn.implicitHeight + 24 : 0
                    property var scopes: win.expert || line.r.scope !== "part"
                        ? [{ value: "part", label: "эта деталь" }, { value: "material", label: "материал" }, { value: "mode", label: "режим" }, { value: "global", label: "всегда" }]
                        : [{ value: "part", label: "эта деталь" }]
                    property int scopeIndex: 0
                    property string scopeValue: ""
                    readonly property string scope: scopes[Math.min(scopeIndex, scopes.length - 1)].value
                    onVisibleChanged: if (visible) reset()
                    Component.onCompleted: if (visible) reset()
                    function reset() {
                        valueField.text = win.rawText(line.r.value);
                        noteField.text = line.r.note || "";
                        scopeValue = line.r.scope_value || "";
                        scopeIndex = 0;
                        for (var i = 0; i < scopes.length; i++) if (scopes[i].value === line.r.scope) scopeIndex = i;
                    }
                    function save() {
                        var fields = { value: win.parseValue(valueField.text, line.r.value), scope: scope, note: noteField.text.trim() };
                        if (scope === "material" || scope === "mode") fields.scope_value = scopeValue;
                        else if (scope !== line.r.scope) fields.scope_value = scope === "part" ? (line.r.part_name || null) : null;
                        // a saved rule reloads the list and destroys this row: keep the window in a local
                        var w = win;
                        var error = w.memory.updateRule(line.r.id, JSON.stringify(fields));
                        w.editError = error;
                        if (!error) { w.editing = ""; w.say("Правило сохранено", true); }
                    }
                    Rectangle { anchors.fill: parent; color: st.accentSoft; opacity: 0.45 }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: st.borderAccent }

                    Column {
                        id: editColumn
                        x: 12; y: 10
                        width: parent.width - 24
                        spacing: 8
                        Row {
                            spacing: 12
                            Column {
                                spacing: 4
                                Text { text: "ЗНАЧЕНИЕ"; color: st.textSecondary; font.family: st.family; font.pixelSize: 9; font.weight: Font.DemiBold }
                                Rectangle {
                                    width: 140; height: 28; radius: 5; color: "white"
                                    border.width: 1; border.color: win.editError !== "" ? st.danger : st.borderAccent
                                    TextInput {
                                        id: valueField
                                        x: 9; width: parent.width - 18
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: win.editError !== "" ? st.danger : st.accent
                                        font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                                        selectByMouse: true; clip: true
                                        Keys.onReturnPressed: editor.save()
                                    }
                                }
                            }
                            MemoryFilter {
                                title: "Область"; width: 130
                                choices: editor.scopes   // every choice is a real scope, no "все"
                                value: editor.scope
                                onPicked: {
                                    for (var i = 0; i < editor.scopes.length; i++) if (editor.scopes[i].value === value) editor.scopeIndex = i;
                                    editor.scopeValue = value === line.r.scope ? (line.r.scope_value || "")
                                        : value === "material" ? (win.facets.materials.length ? win.facets.materials[0].value : "")
                                        : value === "mode" ? "classic" : "";
                                }
                            }
                            MemoryFilter {
                                visible: editor.scope === "material"
                                title: "Какой материал"; width: 190
                                choices: win.facets.materials.length ? win.facets.materials : [{ value: "", label: "нет материалов в памяти" }]
                                value: editor.scopeValue
                                onPicked: editor.scopeValue = value
                            }
                            MemoryFilter {
                                visible: editor.scope === "mode"
                                title: "Какой режим"; width: 130
                                choices: [{ value: "classic", label: "3D" }, { value: "cylindrical", label: "5D Spiral" }, { value: "cylindrical_full", label: "5D" }]
                                value: editor.scopeValue
                                onPicked: editor.scopeValue = value
                            }
                            Column {
                                spacing: 4
                                Text { text: "ЗАМЕТКА"; color: st.textSecondary; font.family: st.family; font.pixelSize: 9; font.weight: Font.DemiBold }
                                Rectangle {
                                    width: 240; height: 28; radius: 5; color: "white"
                                    border.width: 1; border.color: st.border
                                    TextInput {
                                        id: noteField
                                        x: 9; width: parent.width - 18
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: st.textPrimary
                                        font.family: st.family; font.pixelSize: 12
                                        selectByMouse: true; clip: true
                                        Keys.onReturnPressed: editor.save()
                                    }
                                }
                            }
                            Row {
                                anchors.bottom: parent.bottom
                                spacing: 8
                                ActionButton { label: "Сохранить"; height: 28; onClicked: editor.save() }
                                ActionButton { label: "Отмена"; kind: "secondary"; height: 28; onClicked: { win.editing = ""; win.editError = "" } }
                            }
                        }
                        Text {
                            visible: win.editError !== "" || (!win.expert && line.r.scope !== "part")
                            width: parent.width
                            wrapMode: Text.Wrap
                            text: win.editError !== "" ? "Не сохранено: " + win.editError
                                  : "Правила для материала, режима и «всегда» меняет только Эксперт. Новичок может их выключить или удалить."
                            color: win.editError !== "" ? st.danger : st.textSecondary
                            font.family: st.family; font.pixelSize: 11
                        }
                    }
                }

                Rectangle { visible: !line.isEditing; width: parent.width; height: 1; color: st.border }
            }

            Text {  // nothing to show
                visible: list.count === 0
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 460)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: win.filtered ? "По этим фильтрам ничего нет."
                      : win.tab === "rules" ? "Правил пока нет. Измените значение в карточке изменений и отметьте «Запомнить»."
                      : "Правок пока не было. Здесь появится каждое значение, которое вы поменяли в карточке."
                color: st.textSecondary
                font.family: st.family; font.pixelSize: 13
            }
        }
    }

    // ---- footer: a question (rollback, import) or the result of the last action
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
                wrapMode: Text.Wrap
                maximumLineCount: 2
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
                    visible: !!win.ask && win.ask.kind === "import"
                    label: "Заменить мои правила"; kind: "tertiary"
                    onClicked: { win.ask = null; win.memory.importFile(false) }
                }
                ActionButton {
                    label: win.ask && win.ask.kind === "import" ? "Добавить к моим" : "Откатить"
                    onClicked: {
                        var a = win.ask;
                        win.ask = null;
                        if (a.kind === "import") win.memory.importFile(true);
                        else win.act(win.memory.rollback(a.ts));
                    }
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
                      : "Правку агент применяет сам, только когда это действующее правило. Кандидаты он видит как подсказку."
                color: win.toast === "" ? st.textSecondary : win.toastOk ? st.success : st.danger
                font.family: st.family; font.pixelSize: 12
            }
        }
        ActionButton {
            visible: !win.ask
            anchors.right: parent.right; anchors.rightMargin: 24
            anchors.verticalCenter: parent.verticalCenter
            label: "Закрыть"; kind: "secondary"
            onClicked: win.hide()
        }
    }
}
