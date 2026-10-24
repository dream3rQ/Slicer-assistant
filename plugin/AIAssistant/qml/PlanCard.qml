// The plan card over the 3D view, left of the Stereo Agent panel, after the
// designers' Figma mock-up (sizes ×1: the mock-up draws it at 0.82).
// Data: manager.proposalJson (items with group, current, proposed, options,
// unit, type, minimum/maximum, reason, warning/error; see docs/plan-card.md).
// Buttons call manager.answerRemember(action, overridesJson, skipJson, text,
// rememberJson); the manager calls are the contract. A row the user changed
// gets "Запомнить" with a scope (manager.memory, memory_ui.py); a row that came
// from the user's rules (proposal.applied_rules) gets the "из ваших правок" plaque.
import QtQuick 2.7
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4

Item {
    id: base
    property var topbar: null
    property var panel: null
    property var proposal: JSON.parse(manager.proposalJson)
    property bool explain: false   // the "?" button: reasons and previous values under the rows
    property int errors: 0         // rows whose value is out of range; "Применить" waits for them
    property var memory: manager.memory
    readonly property bool expert: memory ? memory.role === "expert" : false
    // item id -> {rule_id, from, to} for rows the user's rules changed
    property var applied: {
        var out = {}, list = proposal.applied_rules || [];
        for (var i = 0; i < list.length; i++) out[list[i].item_id] = list[i];
        return out;
    }
    Style { id: st }

    // Placed left of the panel until the user drags it by the header; then it stays where it was put.
    readonly property int colNow: 100
    readonly property int colRec: 120
    readonly property int colYours: 170
    width: 600
    x: (panel && panel.visible ? panel.x : (parent ? parent.width : 800)) - width - 7
    y: topbar ? topbar.y + topbar.height + 7 : 7
    height: Math.min(header.height + column.implicitHeight + 3, (parent ? parent.height : 800) - y - 16)
    z: 60
    visible: false
    onProposalChanged: { explain = false; errors = 0 }

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
    function countErrors() {
        var n = 0;
        for (var i = 0; i < rows.count; i++) if (rows.itemAt(i) && rows.itemAt(i).rangeError !== "") n++;
        errors = n;
    }
    // the value of a card row as the user left it ("mode", "material_id")
    function chosen(itemId) {
        for (var i = 0; i < rows.count; i++) {
            var row = rows.itemAt(i);
            if (row && row.item.id === itemId) return row.edited();
        }
        return undefined;
    }
    function itemById(itemId) {
        var items = proposal.items || [];
        for (var i = 0; i < items.length; i++) if (items[i].id === itemId) return items[i];
        return null;
    }
    function materialName() {
        var it = itemById("material_id");
        return it ? fmt(it, chosen("material_id")) : "";
    }
    function memoryContext() {
        return { material: chosen("material_id"), mode: chosen("mode") };
    }
    // "Запомнить" choices: a novice may only remember for this part (plan v0.7.0);
    // the print mode is remembered as a precedent for this part only.
    function scopesFor(item) {
        var out = [{ value: "part", label: item.id === "mode" ? "для этой детали (прецедент)" : "для этой детали" }];
        if (!expert || item.id === "mode") return out;
        var m = materialName();
        out.push({ value: "material", label: m ? "для материала " + m : "для этого материала" });
        out.push({ value: "global", label: "всегда" });
        return out;
    }
    function apply() {
        if (errors > 0) return;
        var overrides = {}, byScope = {};
        for (var i = 0; i < rows.count; i++) {
            var row = rows.itemAt(i);
            var v = row.edited();
            if (v !== undefined && String(v) !== String(row.item.proposed)) overrides[row.item.id] = v;
            if (row.remember && row.memoryError === "" && overrides[row.item.id] !== undefined) {
                var scope = row.scopes[row.scopeIndex].value;
                (byScope[scope] = byScope[scope] || []).push(row.item.id);
            }
        }
        var remember = [];
        for (var k in byScope) remember.push({ keys: byScope[k], scope: k });
        manager.answerRemember("apply", JSON.stringify(overrides), "[]", "", JSON.stringify(remember));
    }

    // glow and frame
    Rectangle { anchors.fill: parent; anchors.margins: -4; radius: 15; color: Qt.rgba(39 / 255, 140 / 255, 226 / 255, 0.06) }
    Rectangle { anchors.fill: parent; anchors.margins: -2; radius: 13; color: Qt.rgba(39 / 255, 140 / 255, 226 / 255, 0.08) }
    Rectangle { anchors.fill: parent; radius: 10.75; color: "white"; border.color: st.borderAccent; border.width: 1.2 }

    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: wheel.accepted = false }

    Item {  // header: drag to move the card; the "?" shows why each value is proposed
        id: header
        x: 1.2; y: 1.2
        width: parent.width - 2.4
        z: 2
        height: 46
        MouseArea {
            anchors.fill: parent
            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
            drag.target: base
            drag.minimumX: 0
            drag.maximumX: base.parent ? base.parent.width - base.width : 0
            drag.minimumY: base.topbar ? base.topbar.y + base.topbar.height : 0
            drag.maximumY: base.parent ? base.parent.height - 60 : 0
        }
        Grid {  // grip
            x: 18; anchors.verticalCenter: parent.verticalCenter
            columns: 3; spacing: 3
            Repeater { model: 6; Rectangle { width: 3; height: 3; radius: 1.5; color: st.iconMuted } }
        }
        Text {
            x: 40; anchors.verticalCenter: parent.verticalCenter
            text: "Изменения настроек"
            color: st.textSecondary
            font.family: st.family; font.pixelSize: 11
        }
        Item {
            anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
            width: 30; height: 30
            Rectangle { anchors.fill: parent; radius: 15; color: base.explain ? st.accentSoft : "transparent" }
            Icon { anchors.centerIn: parent; name: "circle_help"; width: 20; height: 20 }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: base.explain = !base.explain }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: st.border }
    }

    Flickable {
        anchors.fill: parent
        anchors.margins: 1.2
        anchors.topMargin: header.height + 1.2
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: column
            width: parent.width

            Item {
                x: 16
                width: parent.width - 32
                height: body.implicitHeight + 32

                Column {
                    id: body
                    y: 16
                    width: parent.width

                    Row {  // column titles
                        height: 24
                        spacing: 8
                        Text { width: body.width - base.colNow - base.colRec - base.colYours - 24; anchors.verticalCenter: parent.verticalCenter; leftPadding: 2; text: "ПАРАМЕТР"; color: st.textSecondary; font.family: st.family; font.pixelSize: 10 }
                        Text { width: base.colNow; anchors.verticalCenter: parent.verticalCenter; text: "СЕЙЧАС"; color: st.textSecondary; font.family: st.family; font.pixelSize: 10 }
                        Text { width: base.colRec; anchors.verticalCenter: parent.verticalCenter; text: "РЕКОМЕНДАЦИЯ"; color: st.textSecondary; font.family: st.family; font.pixelSize: 10 }
                        Text { width: base.colYours; anchors.verticalCenter: parent.verticalCenter; text: "ВАШЕ ЗНАЧЕНИЕ"; color: st.textSecondary; font.family: st.family; font.pixelSize: 10 }
                    }

                    Repeater {
                        id: rows
                        model: base.proposal.items

                        delegate: Column {
                            id: row
                            property var item: modelData
                            property bool firstInGroup: index === 0 || base.proposal.items[index - 1].group !== item.group
                            property bool hasOptions: !!(item.options && item.options.length) || item.type === "bool"
                            property bool numeric: item.type === "int" || item.type === "float"
                            property var choices: item.options && item.options.length ? item.options
                                                 : [{ value: true, label: "да" }, { value: false, label: "нет" }]
                            property bool userChanged: false
                            readonly property bool differs: !item.unchanged || userChanged
                            // the value typed in is outside the setting's limits
                            property string rangeError: {
                                if (!numeric || hasOptions) return "";
                                var t = field.text.replace(",", ".").trim();
                                var v = parseFloat(t);
                                var u = item.unit ? " " + item.unit : "";
                                var lo = item.minimum, hi = item.maximum;
                                var limits = lo !== undefined && hi !== undefined ? "от " + base.num(lo) + " до " + base.num(hi) + u
                                           : lo !== undefined ? "не меньше " + base.num(lo) + u
                                           : hi !== undefined ? "не больше " + base.num(hi) + u : "";
                                if (t === "" || isNaN(v)) return "Введите число" + (limits ? " " + limits : "") + ".";
                                if ((lo !== undefined && v < lo) || (hi !== undefined && v > hi))
                                    return "Введите значение " + limits + ". Сейчас указано " + base.num(v) + u + ".";
                                return "";
                            }
                            onRangeErrorChanged: base.countErrors()
                            readonly property string problem: rangeError || item.error || ""
                            // "Запомнить": only for a value the user changed, and only a valid one
                            property bool remember: false
                            property int scopeIndex: 0
                            readonly property var scopes: base.scopesFor(item)
                            readonly property bool canRemember: userChanged && rangeError === "" && !item.error && !!base.memory
                            onCanRememberChanged: if (!canRemember) remember = false
                            onScopesChanged: if (scopeIndex >= scopes.length) scopeIndex = 0
                            property string memoryError: {
                                if (!remember || !canRemember) return "";
                                var deps = field.text + combo.currentIndex + scopeIndex;
                                var ctx = base.memoryContext();
                                ctx.scope = scopes[Math.min(scopeIndex, scopes.length - 1)].value;
                                var r = JSON.parse(base.memory.validate(item.key || item.id, JSON.stringify(edited()), JSON.stringify(ctx)));
                                return r.ok ? "" : r.reason;
                            }
                            readonly property var fromRule: base.applied[item.id] || null
                            property bool ruleOpen: false
                            property var rule: ruleOpen && fromRule && base.memory ? JSON.parse(base.memory.rule(fromRule.rule_id)) : null

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
                            width: body.width

                            Rectangle {  // section title
                                visible: row.firstInGroup
                                width: parent.width; height: visible ? 19 : 0
                                color: st.groupBg
                                Text { x: 7; anchors.verticalCenter: parent.verticalCenter; text: (row.item.group || "").toUpperCase(); color: st.textSecondary; font.family: st.family; font.pixelSize: 9; font.weight: Font.DemiBold }
                            }

                            Row {
                                height: 34
                                spacing: 8

                                Text {
                                    width: body.width - base.colNow - base.colRec - base.colYours - 24
                                    anchors.verticalCenter: parent.verticalCenter
                                    leftPadding: 2
                                    text: row.item.label
                                    elide: Text.ElideRight
                                    color: st.textPrimary
                                    font.family: st.family; font.pixelSize: 11
                                    font.weight: row.problem !== "" ? Font.DemiBold : Font.Normal
                                }
                                Text {  // what the slicer has now
                                    width: base.colNow
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: base.fmt(row.item, row.item.current)
                                    elide: Text.ElideRight
                                    color: st.textSecondary
                                    font.family: st.family; font.pixelSize: 11
                                }
                                Text {  // what the assistant proposes; "оставить" when it is the same
                                    width: base.colRec
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: row.item.unchanged ? "оставить" : base.fmt(row.item, row.item.proposed)
                                    elide: Text.ElideRight
                                    color: row.item.unchanged ? st.textSecondary : st.accent
                                    font.family: st.family; font.pixelSize: 11
                                    font.weight: row.item.unchanged ? Font.Normal : Font.Medium
                                }

                                Rectangle {  // "your value": a number with its unit and ↕, or a list with ⌄
                                    width: base.colYours; height: 30
                                    anchors.verticalCenter: parent.verticalCenter
                                    radius: 5
                                    color: "white"
                                    border.width: 1
                                    border.color: row.problem !== "" ? st.danger : row.differs ? st.borderAccent : st.border
                                    readonly property color valueColor: row.problem !== "" ? st.danger : row.differs ? st.accent : st.textPrimary

                                    TextInput {
                                        id: field
                                        visible: !row.hasOptions
                                        x: 9; width: parent.width - 9 - unit.width - 34
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: row.item.proposed === null || row.item.proposed === undefined ? "" : base.num(row.item.proposed)
                                        color: parent.valueColor
                                        font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                                        selectByMouse: true
                                        clip: true
                                        onTextChanged: row.userChanged = String(row.edited()) !== String(row.item.proposed)
                                        Keys.onUpPressed: row.step(1)
                                        Keys.onDownPressed: row.step(-1)
                                    }
                                    Text {
                                        id: unit
                                        visible: !row.hasOptions
                                        anchors.right: parent.right; anchors.rightMargin: row.numeric ? 26 : 9
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: row.item.unit || ""
                                        color: st.textSecondary
                                        font.family: st.family; font.pixelSize: 11
                                    }
                                    Item {  // ↕: the top half adds a step, the bottom half takes one away
                                        visible: !row.hasOptions && row.numeric
                                        anchors.right: parent.right; anchors.rightMargin: 4
                                        width: 20; height: parent.height
                                        Icon { anchors.centerIn: parent; name: "chevrons_up_down"; width: 12; height: 12 }
                                        MouseArea { width: parent.width; height: parent.height / 2; cursorShape: Qt.PointingHandCursor; onClicked: row.step(1) }
                                        MouseArea { y: parent.height / 2; width: parent.width; height: parent.height / 2; cursorShape: Qt.PointingHandCursor; onClicked: row.step(-1) }
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
                                            background: Item {
                                                Icon { anchors.right: parent.right; anchors.rightMargin: 9; anchors.verticalCenter: parent.verticalCenter; name: "chevron_down"; width: 12; height: 12 }
                                            }
                                            label: Text {
                                                leftPadding: 3
                                                text: control.currentText
                                                color: row.differs ? st.accent : st.textPrimary
                                                font.family: st.family; font.pixelSize: 12; font.weight: Font.Medium
                                                verticalAlignment: Text.AlignVCenter
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }
                                }
                            }

                            Item {  // "Запомнить" with a scope, or the "из ваших правок" plaque
                                visible: row.canRemember || !!row.fromRule
                                width: parent.width
                                height: visible ? 28 : 0
                                Row {
                                    x: 2
                                    height: parent.height
                                    spacing: 10
                                    Item {  // the tick box
                                        visible: row.canRemember
                                        width: rememberLabel.x + rememberLabel.implicitWidth; height: parent.height
                                        Rectangle {
                                            id: box
                                            anchors.verticalCenter: parent.verticalCenter
                                            width: 14; height: 14; radius: 3
                                            color: row.remember ? st.accent : "white"
                                            border.width: 1
                                            border.color: row.remember ? st.accent : st.borderAccent
                                            Canvas {
                                                anchors.fill: parent
                                                visible: row.remember
                                                onPaint: {
                                                    var c = getContext("2d");
                                                    c.reset();
                                                    c.lineWidth = 1.6; c.lineCap = "round"; c.lineJoin = "round";
                                                    c.strokeStyle = "white";
                                                    c.beginPath(); c.moveTo(3.5, 7.2); c.lineTo(6, 9.6); c.lineTo(10.6, 4.4); c.stroke();
                                                }
                                            }
                                        }
                                        Text {
                                            id: rememberLabel
                                            x: box.width + 7
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: "Запомнить"
                                            color: st.accent
                                            font.family: st.family; font.pixelSize: 11; font.weight: Font.Medium
                                        }
                                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: row.remember = !row.remember }
                                    }
                                    Text {  // one choice: no list to open
                                        visible: row.canRemember && row.remember && row.scopes.length === 1
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: row.scopes[0].label
                                        color: st.textPrimary
                                        font.family: st.family; font.pixelSize: 11
                                    }
                                    Rectangle {
                                        visible: row.canRemember && row.remember && row.scopes.length > 1
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: 250; height: 24; radius: 5
                                        color: "white"; border.width: 1; border.color: st.borderAccent
                                        ComboBox {
                                            id: scopeCombo
                                            anchors.fill: parent
                                            model: row.scopes.map(function (o) { return o.label })
                                            // by hand: Controls 1 puts currentIndex back to 0 when the model changes
                                            onModelChanged: currentIndex = row.scopeIndex
                                            Component.onCompleted: currentIndex = row.scopeIndex
                                            Connections { target: row; onScopeIndexChanged: scopeCombo.currentIndex = row.scopeIndex }
                                            onActivated: row.scopeIndex = index
                                            style: ComboBoxStyle {
                                                background: Item {
                                                    Icon { anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; name: "chevron_down"; width: 10; height: 10 }
                                                }
                                                label: Text {
                                                    leftPadding: 2
                                                    text: control.currentText
                                                    color: st.textPrimary
                                                    font.family: st.family; font.pixelSize: 11
                                                    verticalAlignment: Text.AlignVCenter
                                                    elide: Text.ElideRight
                                                }
                                            }
                                        }
                                    }
                                    Text {
                                        visible: row.canRemember && row.remember && !base.expert && row.item.id !== "mode"
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "на материал и «всегда» запоминает Эксперт"
                                        color: st.textSecondary
                                        font.family: st.family; font.pixelSize: 10
                                    }
                                    Rectangle {  // the plaque: click shows the rule
                                        visible: !row.canRemember && !!row.fromRule
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: plaque.implicitWidth + 16; height: 20; radius: 10
                                        color: st.accentSoft
                                        border.width: row.ruleOpen ? 1 : 0; border.color: st.borderAccent
                                        Text {
                                            id: plaque
                                            anchors.centerIn: parent
                                            text: "из ваших правок"
                                            color: st.accent
                                            font.family: st.family; font.pixelSize: 10; font.weight: Font.Medium
                                        }
                                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: row.ruleOpen = !row.ruleOpen }
                                    }
                                    Text {
                                        visible: !row.canRemember && !!row.fromRule && !row.ruleOpen
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: row.fromRule ? "агент предлагал " + base.fmt(row.item, row.fromRule.from) : ""
                                        color: st.textSecondary
                                        font.family: st.family; font.pixelSize: 10
                                    }
                                }
                            }
                            Text {  // the rule behind the plaque
                                visible: row.ruleOpen && !!row.fromRule && !row.canRemember
                                width: parent.width
                                height: visible ? implicitHeight + 8 : 0
                                leftPadding: 2
                                wrapMode: Text.Wrap
                                textFormat: Text.StyledText
                                linkColor: st.accent
                                font.family: st.family; font.pixelSize: 11
                                color: st.textSecondary
                                text: !row.rule ? "Правило уже удалено. <a href=\"open\">Память агента</a>"
                                      : "Правило: " + row.rule.key_label + " = " + row.rule.value_text + ", " + row.rule.scope_text
                                        + ". Создал " + row.rule.role_text + " " + row.rule.created
                                        + (row.rule.note ? ". «" + row.rule.note + "»" : "")
                                        + ". Агент предлагал " + base.fmt(row.item, row.fromRule.from) + ". <a href=\"open\">Память агента</a>"
                                onLinkActivated: base.memory.open()
                            }
                            Text {  // the knowledge base said no: the rule will not be kept
                                visible: row.memoryError !== ""
                                width: parent.width
                                height: visible ? implicitHeight + 6 : 0
                                leftPadding: 2
                                wrapMode: Text.Wrap
                                text: "Не запомнится: " + row.memoryError + ". Значение применится только сейчас."
                                color: st.danger
                                font.family: st.family; font.pixelSize: 11
                            }
                            Text {  // the error under the row (mock-up frame 4)
                                visible: row.problem !== ""
                                width: parent.width
                                height: visible ? Math.max(24, implicitHeight + 8) : 0
                                leftPadding: 2
                                verticalAlignment: Text.AlignVCenter
                                wrapMode: Text.Wrap
                                text: row.problem
                                color: st.danger
                                font.family: st.family; font.pixelSize: 11
                            }
                            Text {  // "?": why, what it was; warnings always
                                visible: row.problem === "" && (base.explain || !!row.item.warning)
                                width: parent.width
                                height: visible ? implicitHeight + 6 : 0
                                leftPadding: 2
                                wrapMode: Text.Wrap
                                font.family: st.family; font.pixelSize: 11
                                color: row.item.warning && !base.explain ? st.warning : st.textSecondary
                                text: {
                                    var parts = [];
                                    if (base.explain && row.item.reason) parts.push(row.item.reason);
                                    if (row.item.warning) parts.push(row.item.warning);
                                    return parts.join(" · ");
                                }
                            }
                        }
                    }

                    Item { width: 1; height: 22 }

                    Row {  // footer
                        anchors.right: parent.right
                        spacing: 10
                        Rectangle {  // not in the mock-up: without it the agent would wait for an answer
                            width: rejectText.implicitWidth + 32; height: 52; radius: 8
                            color: "white"; border.color: st.border; border.width: 1
                            opacity: rejectArea.pressed ? 0.75 : 1
                            Text { id: rejectText; anchors.centerIn: parent; text: "Отклонить"; color: st.textPrimary; font.family: st.family; font.pixelSize: 15; font.weight: Font.DemiBold }
                            MouseArea { id: rejectArea; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: manager.answerRemember("reject", "{}", "[]", "", "[]") }
                        }
                        Rectangle {
                            width: 213; height: 52; radius: 8
                            color: st.accent
                            opacity: base.errors > 0 ? 0.45 : applyArea.pressed ? 0.8 : 1
                            Text { anchors.centerIn: parent; text: "Применить изменения"; color: "white"; font.family: st.family; font.pixelSize: 15; font.weight: Font.DemiBold }
                            MouseArea { id: applyArea; anchors.fill: parent; cursorShape: base.errors > 0 ? Qt.ForbiddenCursor : Qt.PointingHandCursor; onClicked: base.apply() }
                        }
                    }
                }
            }
        }
    }
}
