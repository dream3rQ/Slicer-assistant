// Stereo Agent: the assistant panel on the right of the slicer window, after the
// designers' Figma mock-up (colours and type in Style.qml).
// slicer_ui.mount puts it next to the top bar and sets `topbar`; slicer_chat.py
// is `manager` (events in via eventAdded/cleared, actions out via send/addFiles/...).
// The manager calls are the contract (docs/slicer-ui.md).
import QtQuick 2.7
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4
import QtQuick.Layouts 1.1
import QtQuick.Window 2.2

Rectangle {
    id: base
    property var topbar: null
    Style { id: st }

    // the user drags the left edge; slicer_chat keeps the width in the settings
    readonly property int minWidth: 300
    readonly property int maxWidth: parent ? Math.max(minWidth, parent.width * 0.6) : 700
    readonly property int pad: 28      // side padding of the content, as in the mock-up
    width: Math.max(minWidth, Math.min(maxWidth, manager.panelWidth))
    x: parent ? parent.width - width : 0
    y: topbar ? topbar.y + topbar.height : 0
    height: parent ? parent.height - y : 600
    z: 50
    visible: false
    color: "white"

    // the soft blue gradient over the lower part of the panel
    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: Math.min(512, parent.height)
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#00FFFFFF" }
            GradientStop { position: 0.5; color: "#66EEF7FF" }
            GradientStop { position: 1.0; color: "#14EAF3FD" }
        }
    }
    Rectangle { width: 1; height: parent.height; color: st.borderAccent; z: 5 }  // left border

    // swallow clicks and wheel so they do not reach the 3D view underneath
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: wheel.accepted = true }

    // resize handle on the left edge
    MouseArea {
        id: grip
        width: 8; height: parent.height; x: -3; z: 10
        cursorShape: Qt.SizeHorCursor
        hoverEnabled: true
        property real startX: 0
        property real startWidth: 0
        onPressed: { startX = mapToItem(null, mouse.x, 0).x; startWidth = base.width }
        onPositionChanged: {
            if (!pressed) return;
            var w = startWidth + startX - mapToItem(null, mouse.x, 0).x;
            base.width = Math.max(base.minWidth, Math.min(base.maxWidth, w));
        }
        onReleased: manager.savePanelWidth(base.width)
        Rectangle { x: 3; width: 2; height: parent.height; color: st.accent; visible: grip.containsMouse || grip.pressed }
    }

    // ---- state built from the session's events
    property var callRows: ({})
    property var proposalRows: ({})
    property string openProposal: ""
    property string thinking: ""
    property real progress: -1
    property string mode: ""   // "", "3D" or "5D": the switch in the input card
    property bool fileDrag: false   // a file is dragged over the panel (set by slicer_chat.eventFilter)
    property var attachments: JSON.parse(manager.attachmentsJson)

    function addRow(row) {
        var defaults = { text: "", extra: "", files: "", summary: "", details: "", title: "", actions: "" };
        for (var k in defaults) if (row[k] === undefined) row[k] = defaults[k];
        messages.append(row);
        list.positionViewAtEnd();
        return messages.count - 1;
    }

    // What an error means for the user, and what they can do about it (mock-up frame 6).
    function errorRow(text) {
        var row = { kind: "error", text: text, title: "Что-то пошло не так", actions: "retry" };
        if (/подключиться|SSL|не JSON|пустой ответ/i.test(text)) row.title = "Нет связи с моделью";
        else if (/Не задан URL|ключ[аи]?\b|лимит использования|настройк/i.test(text)) { row.title = "Нужны настройки модели"; row.actions = "settings,retry"; }
        else if (/HTTP/.test(text)) row.title = "Модель вернула ошибку";
        else if (/лимит шагов/i.test(text)) { row.title = "Задача длиннее лимита шагов"; row.actions = "continue"; }
        else if (/ещё отвечает|Повторять нечего|Речь не распознана|микрофон/i.test(text)) row.actions = "";
        return row;
    }

    function onEvent(ev) {
        var d = ev.data || {};
        if (ev.type !== "thinking") thinking = "";
        switch (ev.type) {
        case "user":
            var files = (d.attachments || []).map(function (a) { return a.name; }).join("\n");
            var modeLabel = d.hint ? (d.hint.indexOf("5D") >= 0 ? "режим 5D" : "режим 3D") : "";
            addRow({ kind: "user", text: d.text || "", extra: modeLabel, files: files }); break;
        case "assistant":
            addRow({ kind: "assistant", text: d.html || "" }); break;
        case "note":   // the agent's words between tool calls: markdown like an answer
            addRow({ kind: "note", text: d.html || d.text || "" }); break;
        case "status":
            addRow({ kind: "status", text: d.text || "" }); break;
        case "error":
            addRow(errorRow(d.text || "")); break;
        case "thinking":
            thinking = "Думаю… шаг " + d.step + (d.tokens ? " · " + d.tokens + " токенов" : ""); break;
        case "tool_call":
            callRows[d.id] = addRow({ kind: "tool", text: d.name, summary: "…" }); break;
        case "tool_result":
            var i = callRows[d.id];
            if (i !== undefined) {
                messages.setProperty(i, "summary", d.summary);
                messages.setProperty(i, "details", d.details || "");
                messages.setProperty(i, "extra", d.ok ? "ok" : "fail");
            }
            if (d.saved) addRow({ kind: "saved", text: d.saved, details: d.saved_dir || "" });
            break;
        case "progress":
            progress = d.result ? -1 : (d.value || 0); break;
        case "proposal":
            openProposal = d.id;
            proposalRows[d.id] = addRow({ kind: "proposal", text: "Предлагаю изменения: " + d.items.length + ". Проверьте их в карточке.", extra: "open" });
            break;
        case "proposal_closed":
            if (openProposal === d.id) openProposal = "";
            var p = proposalRows[d.id];
            if (p !== undefined) {
                messages.setProperty(p, "extra", "closed");
                messages.setProperty(p, "summary", { apply: "применено", revise: "отправлено на уточнение", reject: "отклонено", cancel: "остановлено" }[d.action] || d.action);
            }
            break;
        case "usage":
            addRow({ kind: "usage", text: d.line || "" }); break;
        case "idle":
            progress = -1; break;
        }
    }

    function send() {
        if (input.text.trim() === "" && attachments.length === 0) return;
        manager.send(input.text, mode);
        input.text = "";
    }
    function act(action) {
        if (action === "settings") settings.open();
        else manager.retry();   // "retry" and "continue"
    }

    Connections {
        target: manager
        onEventAdded: base.onEvent(JSON.parse(json))
        onCleared: { messages.clear(); callRows = {}; proposalRows = {}; openProposal = ""; thinking = ""; progress = -1 }
        onConfigTested: { settings.status = text; settings.statusOk = ok }
        onVoiceText: { input.text = (input.text.trim() ? input.text.trim() + " " : "") + text; input.forceActiveFocus() }
        onSettingsRequested: settings.open()
    }

    ListModel { id: messages }

    // ---- header: "Stereo Agent" and × on the empty panel; › (hide) and the title on the right in a conversation
    Item {
        id: header
        width: parent.width
        height: 44
        readonly property bool working: messages.count > 0
        Text {
            anchors.verticalCenter: parent.verticalCenter
            x: header.working ? parent.width - width - 23 : 18
            text: "Stereo Agent"
            color: st.accent
            font.family: st.family; font.pixelSize: 14; font.weight: Font.Medium
        }
        Item {
            visible: header.working
            x: 10; width: 24; height: parent.height
            Icon { anchors.centerIn: parent; name: "collapse_chevron"; width: 5; height: 9 }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: manager.hide() }
        }
        Item {
            visible: !header.working
            anchors.right: parent.right; anchors.rightMargin: 13
            width: 30; height: parent.height
            Icon { anchors.centerIn: parent; name: "close"; width: 10; height: 10 }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: manager.hide() }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: base.fileDrag ? st.borderAccent : st.border }
    }

    // ---- empty state: logo, greeting and quick prompts
    Column {
        visible: messages.count === 0 && !base.fileDrag
        y: 89
        x: base.pad
        width: parent.width - 2 * base.pad
        spacing: 0

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 44; height: 44; radius: 22; color: st.accentSoft
            Icon { anchors.centerIn: parent; name: "agent_logo"; width: 44; height: 44 }
        }
        Item { width: 1; height: 6 }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "С чем поработаем сегодня?"
            color: "black"
            font.family: st.family; font.pixelSize: 16
        }
        Item { width: 1; height: 24 }
        QuickGroup {
            title: "Подобрать конкретный параметр"
            onPicked: { input.text = text; input.forceActiveFocus() }
            prompts: [
                { label: "температура", text: "Подбери температуру сопла и стола под материал и объясни почему" },
                { label: "прочность", text: "Какое заполнение и сколько стенок поставить, чтобы деталь была прочной?" },
                { label: "поддержка", text: "Нужны ли этой детали поддержки и какие?" }
            ]
        }
        Item { width: 1; height: 10 }
        QuickGroup {
            title: "Сократить время"
            onPicked: { input.text = text; input.forceActiveFocus() }
            prompts: [
                { label: "без потери прочности", text: "Сократи время печати без потери прочности" },
                { label: "внешний вид не важен", text: "Ускорь печать: внешний вид не важен" },
                { label: "что влияет сильнее всего", text: "Что сильнее всего влияет на время печати этой детали?" }
            ]
        }
        Item { width: 1; height: 16 }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "Модель, фото детали или ТЗ можно перетащить на панель или приложить через «+»"
            color: st.textSecondary
            font.family: st.family; font.pixelSize: 11
        }
    }

    // ---- messages
    ScrollView {
        id: scroll
        visible: messages.count > 0 && !base.fileDrag
        anchors.top: header.bottom
        anchors.topMargin: 16
        anchors.bottom: composer.top
        anchors.bottomMargin: 12
        x: base.pad - 4
        width: parent.width - 2 * base.pad + 12
        horizontalScrollBarPolicy: Qt.ScrollBarAlwaysOff

        ListView {
            id: list
            model: messages
            spacing: 12
            delegate: Column {
                id: rowItem
                width: list.width - 12
                x: 4
                property bool expanded: false

                Item {  // user: a light bubble on the right
                    visible: kind === "user"
                    width: parent.width
                    height: visible ? bubble.height : 0
                    Rectangle {
                        id: bubble
                        anchors.right: parent.right
                        width: Math.min(parent.width * 0.88, model.files !== "" ? parent.width * 0.88 : Math.max(userMetrics.advanceWidth + 2, att.implicitWidth) + 24)
                        height: bubbleCol.height + 16
                        radius: 10
                        color: st.accentSoft
                        TextMetrics { id: userMetrics; text: model.text; font: userText.font }
                        Column {
                            id: bubbleCol
                            x: 12; y: 8
                            width: parent.width - 24
                            spacing: 6
                            Text { id: userText; width: parent.width; visible: model.text !== ""; text: model.text; wrapMode: Text.Wrap; color: st.textPrimary; font.family: st.family; font.pixelSize: 13 }
                            FileChips { width: parent.width; names: model.files ? model.files.split("\n") : []; visible: model.files !== "" }
                            Text { id: att; text: model.extra; visible: model.extra !== ""; color: st.accent; font.family: st.family; font.pixelSize: 11 }
                        }
                    }
                }
                Text {  // assistant answer and notes, rich text prepared by slicer_chat.markdown_html
                    visible: kind === "assistant" || kind === "note"
                    width: parent.width
                    text: visible ? model.text : ""
                    textFormat: Text.RichText
                    wrapMode: Text.Wrap
                    color: kind === "note" ? st.textSecondary : st.textPrimary
                    font.family: st.family; font.pixelSize: 13
                    lineHeight: 1.15
                    onLinkActivated: Qt.openUrlExternally(link)
                }
                Text {  // status, usage
                    visible: kind === "status" || kind === "usage"
                    width: parent.width
                    text: visible ? model.text : ""
                    wrapMode: Text.Wrap
                    font.italic: kind === "status"
                    font.family: st.family
                    font.pixelSize: kind === "usage" ? 10 : 12
                    color: st.textSecondary
                }
                Column {  // error: pink warning with a title and actions (mock-up frames 5-6)
                    visible: kind === "error"
                    width: parent.width
                    spacing: 16
                    Rectangle {
                        width: parent.width
                        height: visible ? alertCol.height + 24 : 0
                        radius: 6
                        color: st.dangerBg
                        Icon { x: 12; y: 13; name: "triangle_alert"; width: 16; height: 16 }
                        Column {
                            id: alertCol
                            x: 38; y: 12
                            width: parent.width - 50
                            spacing: 3
                            Text { width: parent.width; text: model.title; wrapMode: Text.Wrap; color: st.danger; font.family: st.family; font.pixelSize: 12; font.weight: Font.DemiBold }
                            Text { width: parent.width; text: kind === "error" ? model.text : ""; wrapMode: Text.Wrap; color: st.danger; font.family: st.family; font.pixelSize: 12 }
                        }
                    }
                    Row {
                        visible: model.actions !== "" && index === messages.count - 1 - (messages.count > 0 && messages.get(messages.count - 1).kind === "usage" ? 1 : 0)
                        height: visible ? 30 : 0
                        spacing: 8
                        ActionButton { visible: model.actions.indexOf("settings") >= 0; label: "Настройки"; kind: "secondary"; onClicked: base.act("settings") }
                        ActionButton { visible: model.actions.indexOf("retry") >= 0; label: "Повторить анализ"; kind: "primary"; onClicked: base.act("retry") }
                        ActionButton { visible: model.actions.indexOf("continue") >= 0; label: "Продолжить"; kind: "primary"; onClicked: base.act("continue") }
                    }
                }
                Row {  // a file was saved (mock-up frame 6)
                    visible: kind === "saved"
                    width: parent.width
                    spacing: 8
                    Icon { name: "file_check"; width: 16; height: 16 }
                    Text {
                        width: parent.width - 24
                        text: kind === "saved" ? model.text : ""
                        wrapMode: Text.WrapAnywhere
                        color: savedArea.containsMouse ? st.accent : st.textSecondary
                        font.family: st.family; font.pixelSize: 11
                        MouseArea { id: savedArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: if (model.details) Qt.openUrlExternally("file:///" + model.details.replace(/\\/g, "/").replace(/^\/+/, "")) }
                    }
                }
                Text {  // tool call; click shows the raw result
                    visible: kind === "tool"
                    width: parent.width
                    text: (rowItem.expanded ? "▾ " : "▸ ") + model.text + " → " + model.summary
                    elide: Text.ElideRight
                    font.family: st.family; font.pixelSize: 11
                    color: model.extra === "fail" ? st.danger : st.textSecondary
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: rowItem.expanded = !rowItem.expanded }
                }
                TextArea {
                    visible: kind === "tool" && rowItem.expanded && model.details !== ""
                    width: parent.width
                    height: visible ? 140 : 0
                    readOnly: true
                    text: visible ? model.details : ""
                    font.pixelSize: 10
                }
                Rectangle {  // the plan card's trace in the chat
                    visible: kind === "proposal"
                    width: parent.width
                    height: visible ? pr.implicitHeight + 20 : 0
                    radius: 6
                    border.color: model.extra === "open" ? st.borderAccent : st.border
                    color: "white"
                    RowLayout {
                        id: pr
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 8
                        Text { text: model.text + (model.summary ? " — " + model.summary : ""); wrapMode: Text.Wrap; Layout.fillWidth: true; color: st.textPrimary; font.family: st.family; font.pixelSize: 12 }
                        ActionButton { visible: model.extra === "open"; label: "Показать"; kind: "tertiary"; onClicked: manager.showCard() }
                    }
                }
            }
            footer: Text {
                width: list.width
                text: base.thinking
                visible: base.thinking !== ""
                height: visible ? implicitHeight + 12 : 0
                verticalAlignment: Text.AlignBottom
                color: st.textSecondary
                font.italic: true
                font.family: st.family; font.pixelSize: 12
            }
        }
    }

    // ---- drop zone, shown while files are dragged over the panel (mock-up frame 7)
    onVisibleChanged: fileDrag = false
    Rectangle {  // while dragging, the drop zone covers the messages
        visible: base.fileDrag
        anchors.top: header.bottom
        anchors.bottom: composer.top
        width: parent.width
        color: "white"
    }
    Canvas {
        id: dashed
        visible: base.fileDrag
        z: 1
        x: base.pad
        y: header.height + 33
        width: parent.width - 2 * base.pad
        height: composer.y - y - 32
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var c = getContext("2d");
            c.reset();
            c.strokeStyle = String(st.borderAccent);
            c.lineWidth = 2;
            if (c.setLineDash) c.setLineDash([8, 8]);
            var r = 15, w = width - 1, h = height - 1;
            c.beginPath();
            c.moveTo(1 + r, 1); c.lineTo(w - r, 1); c.arcTo(w, 1, w, 1 + r, r);
            c.lineTo(w, h - r); c.arcTo(w, h, w - r, h, r);
            c.lineTo(1 + r, h); c.arcTo(1, h, 1, h - r, r);
            c.lineTo(1, 1 + r); c.arcTo(1, 1, 1 + r, 1, r);
            c.stroke();
        }
        Column {
            anchors.centerIn: parent
            spacing: 6
            Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "file_upload"; width: 61; height: 61 }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Перетащите файлы"; color: st.borderAccent; font.family: st.family; font.pixelSize: 15; font.weight: Font.DemiBold }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                width: dashed.width - 48
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: "Отпустите здесь: модель, фото или ТЗ получит ассистент, на стол они не попадут"
                color: st.textSecondary
                font.family: st.family; font.pixelSize: 11
            }
        }
    }

    // ---- composer: progress, hint, input card with attachments
    Column {
        id: composer
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 28
        x: base.pad
        width: parent.width - 2 * base.pad
        spacing: 8

        ProgressBar { width: parent.width; visible: base.progress >= 0; value: Math.max(0, base.progress) }

        Text {
            width: parent.width
            visible: base.openProposal !== ""
            text: "Жду решения по карточке. Сообщение отсюда уйдёт как уточнение."
            wrapMode: Text.Wrap
            color: st.accent
            font.family: st.family; font.pixelSize: 11
        }

        Item {
            width: parent.width
            height: card.height

            Rectangle {  // soft shadow under the card
                x: 0; y: 3
                width: card.width; height: card.height
                radius: card.radius
                color: Qt.rgba(0, 0, 0, 0.05)
            }
            Rectangle {
                id: card
                width: parent.width
                height: 124 + (chips.visible ? chips.height + 12 : 0)
                radius: 17.66
                color: "white"
                border.color: st.inputBorder
                border.width: 1.77

                FileChips {
                    id: chips
                    x: 16; y: 14
                    width: parent.width - 32
                    visible: base.attachments.length > 0
                    names: base.attachments.map(function (a) { return a.name; })
                    ids: base.attachments.map(function (a) { return a.id; })
                    onRemove: manager.removeAttachment(id)
                }

                TextArea {
                    id: input
                    x: 14; y: (chips.visible ? chips.y + chips.height + 6 : 12)
                    width: parent.width - 28
                    height: 60
                    frameVisible: false
                    backgroundVisible: false
                    wrapMode: TextEdit.Wrap
                    font.family: st.family; font.pixelSize: 14
                    textColor: st.textPrimary
                    Keys.onReturnPressed: {
                        if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return; }
                        base.send(); event.accepted = true;
                    }
                    Keys.onEnterPressed: { base.send(); event.accepted = true }
                    Keys.onEscapePressed: manager.cancelVoice()
                }
                Text {
                    x: 20; y: input.y + 8
                    visible: input.text === "" && !input.activeFocus
                    text: base.fileDrag ? "Спросить ..." : "Спросить Стерео..."
                    color: st.placeholder
                    font.family: st.family; font.pixelSize: 14
                }

                Row {  // bottom row: attach, 3D/5D
                    anchors.left: parent.left
                    anchors.leftMargin: 16
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 15
                    spacing: 16
                    Item {
                        width: 23; height: 23
                        anchors.verticalCenter: parent.verticalCenter
                        Icon { anchors.centerIn: parent; name: "plus_attach"; width: 14; height: 14 }
                        MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: manager.pickFiles() }
                    }
                    ModeChip { anchors.verticalCenter: parent.verticalCenter; mode: base.mode; onPicked: base.mode = mode }
                }
                Row {  // bottom row: microphone, send / stop
                    anchors.right: parent.right
                    anchors.rightMargin: 13
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 10
                    spacing: 4
                    Item {  // microphone with waves that follow the voice while recording
                        visible: manager.voiceAvailable
                        width: 33; height: 33
                        readonly property real loud: manager.voiceState === "recording" ? Math.max(0, Math.min(1, (manager.voiceLevel - 0.02) * 4)) : 0
                        Repeater {
                            model: 2
                            Rectangle {
                                anchors.centerIn: parent
                                width: 33 + (index + 1) * 10 * parent.loud
                                height: width
                                radius: width / 2
                                color: "transparent"
                                border.width: 2
                                border.color: st.danger
                                opacity: parent.loud > 0.03 ? 0.45 - index * 0.2 : 0
                                Behavior on width { NumberAnimation { duration: 120 } }
                                Behavior on opacity { NumberAnimation { duration: 120 } }
                            }
                        }
                        RoundButton {
                            anchors.centerIn: parent
                            // recording: a red microphone stops it; the square is only for stopping the agent
                            icon: manager.voiceState === "recording" ? "mic_white" : "mic"
                            spinning: manager.voiceState === "transcribing"
                            fill: manager.voiceState === "recording" ? st.danger : st.micBg
                            fg: manager.voiceState === "recording" ? "white" : st.textInk
                            tip: manager.voiceState === "recording" ? "Остановить запись (сама остановится после паузы в 4 секунды)" : "Голос: нажмите и говорите. Запись остановится после паузы в 4 секунды, текст появится в поле (Esc — отмена)"
                            onClicked: manager.toggleVoice()
                        }
                    }
                    RoundButton {
                        visible: !manager.busy || base.openProposal !== ""
                        icon: "send"; iconWidth: 33; iconHeight: 33
                        fill: "transparent"
                        tip: "Отправить (Enter)"
                        onClicked: base.send()
                    }
                    RoundButton {
                        visible: manager.busy && base.openProposal === ""
                        label: "■"; fill: st.dangerBg; fg: st.danger; tip: "Остановить"
                        onClicked: manager.cancel()
                    }
                }
            }
        }
    }

    // ---- settings window
    Window {
        id: settings
        title: "Настройки Stereo Agent"
        width: 520
        height: form.implicitHeight + 32
        flags: Qt.Dialog
        color: "white"
        property string status: ""
        property bool statusOk: true
        property var cfg: ({})

        function open() {
            cfg = JSON.parse(manager.configJson());
            url.text = cfg.llm_url || ""; key.text = ""; model.text = cfg.llm_model || "";
            key.placeholderText = cfg.has_key ? "сохранён: " + cfg.key_masked + " (оставьте пустым)" : "введите ключ";
            temp.text = String(cfg.temperature); steps.text = String(cfg.max_steps); results.text = cfg.results_dir || "";
            ssl.checked = !!cfg.ssl_verify; compact.checked = cfg.compact_history !== false;
            confirm.checked = cfg.confirm_plan !== false; popups.checked = cfg.slicer_popups !== false;
            status = ""; show(); raise();
        }
        function save() {
            manager.saveConfig(JSON.stringify({
                llm_url: url.text.trim(), llm_key: key.text.trim(), llm_model: model.text.trim(),
                temperature: parseFloat(temp.text || "0.2"), max_steps: parseInt(steps.text || "30", 10),
                results_dir: results.text.trim(), ssl_verify: ssl.checked, compact_history: compact.checked,
                confirm_plan: confirm.checked, slicer_popups: popups.checked
            }));
            status = "Сохранено"; statusOk = true;
        }

        ColumnLayout {
            id: form
            anchors.fill: parent
            anchors.margins: 16
            spacing: 6
            Label { text: "URL OpenAI-совместимого API" }
            TextField { id: url; Layout.fillWidth: true; placeholderText: "https://host/v1" }
            Label { text: "Ключ API (хранится в настройках слайсера)" }
            TextField { id: key; Layout.fillWidth: true; echoMode: TextInput.Password }
            Label { text: "Модель" }
            TextField { id: model; Layout.fillWidth: true }
            RowLayout {
                Label { text: "Температура" }
                TextField { id: temp; Layout.preferredWidth: 60 }
                Label { text: "Шагов не больше" }
                TextField { id: steps; Layout.preferredWidth: 60 }
            }
            Label { text: "Папка результатов" }
            TextField { id: results; Layout.fillWidth: true }
            CheckBox { id: ssl; text: "Проверять SSL-сертификат" }
            CheckBox { id: compact; text: "Экономить токены (сокращать старую историю)" }
            CheckBox { id: confirm; text: "Показывать изменения перед применением" }
            CheckBox { id: popups; text: "Карточка изменений и уведомления в окне слайсера" }
            Label { text: settings.status; color: settings.statusOk ? "#1E7A4C" : st.danger; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout {
                Button { text: "Проверить подключение"; onClicked: { settings.save(); settings.status = "Проверяю…"; manager.testConnection() } }
                Item { Layout.fillWidth: true }
                Button { text: "Сохранить"; isDefault: true; onClicked: settings.save() }
                Button { text: "Закрыть"; onClicked: settings.hide() }
            }
        }
    }
}
