// Stereo Agent: the assistant panel on the right of the slicer window.
// slicer_ui.mount puts it next to the top bar and sets `topbar`; slicer_chat.py
// is `manager` (events in via eventAdded/cleared, actions out via send/addFiles/...).
// Designers restyle this file; the manager calls are the contract (docs/slicer-ui.md).
import QtQuick 2.7
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4
import QtQuick.Dialogs 1.2
import QtQuick.Layouts 1.1
import QtQuick.Window 2.2

Rectangle {
    id: base
    property var topbar: null

    // ---- look (from the designers' mock-ups)
    readonly property color blue: "#1E6FD9"
    readonly property color blueSoft: "#E3EEFC"
    readonly property color blueLine: "#BBD7F5"
    readonly property color ink: "#16212C"
    readonly property color muted: "#8A94A3"
    readonly property color danger: "#B42318"

    // ---- type: the slicer theme's font (set by slicer_ui.mount) and one size scale
    property string fontFamily: Qt.application.font.family
    readonly property var fs: ({ tiny: 10, small: 11, caption: 12, body: 13, title: 14, heading: 16 })

    // the user drags the left edge; slicer_chat keeps the width in the settings
    readonly property int minWidth: 300
    readonly property int maxWidth: parent ? Math.max(minWidth, parent.width * 0.6) : 700
    width: Math.max(minWidth, Math.min(maxWidth, manager.panelWidth))
    x: parent ? parent.width - width : 0
    y: topbar ? topbar.y + topbar.height : 0
    height: parent ? parent.height - y : 600
    z: 50
    visible: false
    gradient: Gradient {
        GradientStop { position: 0.0; color: "white" }
        GradientStop { position: 1.0; color: "#F2F7FE" }
    }

    Rectangle { width: 1; height: parent.height; color: base.blueLine }  // left border

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
        Rectangle { x: 3; width: 2; height: parent.height; color: base.blue; visible: grip.containsMouse || grip.pressed }
    }

    // ---- state built from the session's events
    property var callRows: ({})
    property var proposalRows: ({})
    property string openProposal: ""
    property string thinking: ""
    property real progress: -1
    property string mode: ""   // "", "3D" or "5D": the chips under the input
    property var attachments: JSON.parse(manager.attachmentsJson)

    function addRow(row) {
        if (row.files === undefined) row.files = "";
        messages.append(row);
        list.positionViewAtEnd();
        return messages.count - 1;
    }

    function onEvent(ev) {
        var d = ev.data || {};
        if (ev.type !== "thinking") thinking = "";
        switch (ev.type) {
        case "user":
            var files = (d.attachments || []).map(function (a) { return a.name; }).join("\n");
            var modeLabel = d.hint ? (d.hint.indexOf("5D") >= 0 ? "режим 5D" : "режим 3D") : "";
            addRow({ kind: "user", text: d.text || "", extra: modeLabel, files: files, summary: "", details: "" }); break;
        case "assistant":
            addRow({ kind: "assistant", text: d.html || "", extra: "", summary: "", details: "" }); break;
        case "note": case "status":
            addRow({ kind: "note", text: d.text || "", extra: "", summary: "", details: "" }); break;
        case "error":
            addRow({ kind: "error", text: d.text || "", extra: "", summary: "", details: "" }); break;
        case "thinking":
            thinking = "Думаю… шаг " + d.step + (d.tokens ? " · " + d.tokens + " токенов" : ""); break;
        case "tool_call":
            callRows[d.id] = addRow({ kind: "tool", text: d.name, extra: "", summary: "…", details: "" }); break;
        case "tool_result":
            var i = callRows[d.id];
            if (i !== undefined) {
                messages.setProperty(i, "summary", d.summary);
                messages.setProperty(i, "details", d.details || "");
                messages.setProperty(i, "extra", d.ok ? "ok" : "fail");
            }
            break;
        case "progress":
            progress = d.result ? -1 : (d.value || 0); break;
        case "proposal":
            openProposal = d.id;
            proposalRows[d.id] = addRow({ kind: "proposal", text: "Предлагаю изменения: " + d.items.length + ". Проверьте их в карточке.", extra: "open", summary: "", details: "" });
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
            addRow({ kind: "usage", text: d.line || "", extra: "", summary: "", details: "" }); break;
        case "idle":
            progress = -1; break;
        }
    }

    function send() {
        if (input.text.trim() === "" && attachments.length === 0) return;
        manager.send(input.text, mode);
        input.text = "";
    }

    Connections {
        target: manager
        onEventAdded: base.onEvent(JSON.parse(json))
        onCleared: { messages.clear(); callRows = {}; proposalRows = {}; openProposal = ""; thinking = ""; progress = -1 }
        onConfigTested: { settings.status = text; settings.statusOk = ok }
        onVoiceText: { input.text = (input.text.trim() ? input.text.trim() + " " : "") + text; input.forceActiveFocus() }
    }

    ListModel { id: messages }

    // ---- header
    Item {
        id: header
        width: parent.width
        height: 44
        Text { anchors.verticalCenter: parent.verticalCenter; x: 18; text: "Stereo Agent"; color: base.blue; font.family: base.fontFamily; font.pixelSize: base.fs.title }
        Row {
            anchors.right: parent.right
            anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            IconButton { label: "⋯"; tip: "Меню"; onClicked: headerMenu.popup() }
            IconButton { label: "✕"; tip: "Скрыть панель"; onClicked: manager.hide() }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: base.blueLine }
    }

    Menu {
        id: headerMenu
        MenuItem { text: "Новый диалог"; onTriggered: manager.newConversation() }
        MenuItem { text: "Открыть в браузере"; onTriggered: manager.openInBrowser() }
        MenuSeparator {}
        MenuItem { text: "Настройки"; onTriggered: settings.open() }
    }

    // ---- empty state: greeting and quick prompts
    Column {
        visible: messages.count === 0 && !drop.containsDrag
        anchors.top: header.bottom
        anchors.topMargin: 72
        x: 28
        width: parent.width - 56
        spacing: 12

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 44; height: 44; radius: 22; color: base.blueSoft
            Text { anchors.centerIn: parent; text: "⚙"; color: base.blue; font.pixelSize: 26 }
        }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "С чем поработаем сегодня?"; color: base.ink; font.family: base.fontFamily; font.pixelSize: base.fs.heading }
        Item { width: 1; height: 4 }
        QuickGroup {
            title: "Подобрать конкретный параметр"
            onPicked: { input.text = text; input.forceActiveFocus() }
            prompts: ["Какое заполнение поставить для этой детали и почему?", "Подбери температуру сопла и стола под материал", "Нужны ли этой детали поддержки?"]
        }
        QuickGroup {
            title: "Сократить время"
            onPicked: { input.text = text; input.forceActiveFocus() }
            prompts: ["Сократи время печати без потери прочности", "Ускорь печать: внешний вид не важен", "Что сильнее всего влияет на время этой детали?"]
        }
    }

    // ---- messages
    ScrollView {
        id: scroll
        visible: messages.count > 0 && !drop.containsDrag
        anchors.top: header.bottom
        anchors.bottom: composer.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 12
        horizontalScrollBarPolicy: Qt.ScrollBarAlwaysOff

        ListView {
            id: list
            model: messages
            spacing: 10
            delegate: Column {
                id: rowItem
                width: list.width - 8
                property bool expanded: false

                Item {  // user
                    visible: kind === "user"
                    width: parent.width
                    height: visible ? bubble.height : 0
                    Rectangle {
                        id: bubble
                        anchors.right: parent.right
                        width: Math.min(parent.width * 0.88, model.files !== "" ? parent.width * 0.88 : Math.max(userMetrics.advanceWidth + 2, att.implicitWidth) + 24)
                        height: bubbleCol.height + 16
                        radius: 12
                        color: base.blueSoft
                        TextMetrics { id: userMetrics; text: model.text; font: userText.font }
                        Column {
                            id: bubbleCol
                            x: 12; y: 8
                            width: parent.width - 24
                            spacing: 4
                            Text { id: userText; width: parent.width; visible: model.text !== ""; text: model.text; wrapMode: Text.Wrap; color: base.ink; font.family: base.fontFamily; font.pixelSize: base.fs.body }
                            FileChips { id: sentFiles; width: parent.width; names: model.files ? model.files.split("\n") : []; visible: model.files !== ""; fill: "white"; panel: base }
                            Text { id: att; text: model.extra; visible: model.extra !== ""; color: base.blue; font.family: base.fontFamily; font.pixelSize: base.fs.small }
                        }
                    }
                }
                Text {  // assistant answer, rich text prepared by slicer_chat.markdown_html
                    visible: kind === "assistant"
                    width: parent.width
                    text: kind === "assistant" ? model.text : ""
                    textFormat: Text.RichText
                    wrapMode: Text.Wrap
                    color: base.ink
                    font.family: base.fontFamily; font.pixelSize: base.fs.body
                }
                Text {  // note, error, usage
                    visible: kind === "note" || kind === "error" || kind === "usage"
                    width: parent.width
                    text: visible ? model.text : ""
                    wrapMode: Text.Wrap
                    font.italic: kind === "note"
                    font.family: base.fontFamily; font.pixelSize: kind === "usage" ? base.fs.tiny : base.fs.caption
                    color: kind === "error" ? base.danger : base.muted
                }
                Text {  // tool call; click shows the raw result
                    visible: kind === "tool"
                    width: parent.width
                    text: (rowItem.expanded ? "▾ " : "▸ ") + model.text + " → " + model.summary
                    elide: Text.ElideRight
                    font.family: base.fontFamily; font.pixelSize: base.fs.small
                    color: model.extra === "fail" ? base.danger : base.muted
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: rowItem.expanded = !rowItem.expanded }
                }
                TextArea {
                    visible: kind === "tool" && rowItem.expanded && model.details !== ""
                    width: parent.width
                    height: visible ? 140 : 0
                    readOnly: true
                    text: visible ? model.details : ""
                    font.family: base.fontFamily; font.pixelSize: base.fs.tiny
                }
                Rectangle {  // the plan card's trace in the chat
                    visible: kind === "proposal"
                    width: parent.width
                    height: visible ? pr.implicitHeight + 16 : 0
                    radius: 8
                    border.color: model.extra === "open" ? base.blue : base.blueLine
                    color: "white"
                    RowLayout {
                        id: pr
                        anchors.fill: parent
                        anchors.margins: 8
                        Text { text: model.text + (model.summary ? " — " + model.summary : ""); wrapMode: Text.Wrap; Layout.fillWidth: true; color: base.ink; font.family: base.fontFamily; font.pixelSize: base.fs.caption }
                        Text {
                            visible: model.extra === "open"
                            text: "Показать"
                            color: base.blue
                            font.family: base.fontFamily; font.pixelSize: base.fs.caption
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: manager.showCard() }
                        }
                    }
                }
            }
            footer: Text {
                width: list.width
                text: base.thinking
                visible: base.thinking !== ""
                height: visible ? implicitHeight + 8 : 0
                verticalAlignment: Text.AlignBottom
                color: base.muted
                font.italic: true
                font.family: base.fontFamily; font.pixelSize: base.fs.caption
            }
        }
    }

    // ---- drop zone, shown while files are dragged over the panel
    DropArea {
        id: drop
        anchors.top: header.bottom
        anchors.bottom: composer.top
        anchors.left: parent.left
        anchors.right: parent.right
        onDropped: { if (drop.urls.length) manager.addFiles(drop.urls); drop.accept() }
    }
    Canvas {
        id: dashed
        visible: drop.containsDrag
        anchors.fill: drop
        anchors.margins: 28
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var c = getContext("2d");
            c.reset();
            c.strokeStyle = String(base.blueLine);
            c.lineWidth = 2;
            c.setLineDash([6, 5]);
            var r = 14, w = width - 2, h = height - 2;
            c.beginPath();
            c.moveTo(1 + r, 1); c.lineTo(w - r, 1); c.arcTo(w, 1, w, 1 + r, r);
            c.lineTo(w, h - r); c.arcTo(w, h, w - r, h, r);
            c.lineTo(1 + r, h); c.arcTo(1, h, 1, h - r, r);
            c.lineTo(1, 1 + r); c.arcTo(1, 1, 1 + r, 1, r);
            c.stroke();
        }
        Column {
            anchors.centerIn: parent
            spacing: 8
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "⇪"; color: base.blueLine; font.pixelSize: 40 }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Перетащите файлы"; color: base.blueLine; font.family: base.fontFamily; font.pixelSize: base.fs.title; font.bold: true }
        }
    }

    // ---- composer: attachments, input card, progress
    Column {
        id: composer
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 16
        x: 16
        width: parent.width - 32
        spacing: 6

        ProgressBar { width: parent.width; visible: base.progress >= 0; value: Math.max(0, base.progress) }

        Text {
            width: parent.width
            visible: base.openProposal !== ""
            text: "Жду решения по карточке. Сообщение отсюда уйдёт как уточнение."
            wrapMode: Text.Wrap
            color: base.blue
            font.family: base.fontFamily; font.pixelSize: base.fs.small
        }

        FileChips {
            width: parent.width
            visible: base.attachments.length > 0
            names: base.attachments.map(function (a) { return a.name; })
            ids: base.attachments.map(function (a) { return a.id; })
            fill: base.blueSoft
            panel: base
            onRemove: manager.removeAttachment(id)
        }

        Rectangle {
            width: parent.width
            height: 112
            radius: 16
            color: "white"
            border.color: "#E3E8EF"

            TextArea {
                id: input
                x: 10; y: 8
                width: parent.width - 20
                height: 56
                frameVisible: false
                backgroundVisible: false
                wrapMode: TextEdit.Wrap
                font.family: base.fontFamily; font.pixelSize: base.fs.body
                Keys.onReturnPressed: {
                    if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return; }
                    base.send(); event.accepted = true;
                }
                Keys.onEnterPressed: { base.send(); event.accepted = true }
                Keys.onEscapePressed: manager.cancelVoice()
            }
            Text {
                x: 16; y: 14
                visible: input.text === "" && !input.activeFocus
                text: "Спросить Стерео..."
                color: base.muted
                font.family: base.fontFamily; font.pixelSize: base.fs.body
            }

            Row {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 10
                spacing: 8
                Text {
                    text: "+"; color: base.ink; font.pixelSize: 26
                    anchors.verticalCenter: parent.verticalCenter
                    MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: fileDialog.open() }
                }
                ModeChip { label: "3D"; selected: base.mode === "3D"; onClicked: base.mode = base.mode === "3D" ? "" : "3D" }
                ModeChip { label: "5D"; selected: base.mode === "5D"; onClicked: base.mode = base.mode === "5D" ? "" : "5D" }
            }
            Row {
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 10
                spacing: 8
                RoundButton {
                    visible: manager.voiceAvailable
                    label: manager.voiceState === "recording" ? "■" : manager.voiceState === "transcribing" ? "…" : ""
                    mic: manager.voiceState === ""
                    fill: manager.voiceState === "recording" ? base.danger : "#EDEFF2"
                    fg: manager.voiceState === "recording" ? "white" : base.ink
                    tip: "Голос: нажмите и говорите, нажмите ещё раз — текст появится в поле (Esc — отмена)"
                    onClicked: manager.toggleVoice()
                }
                RoundButton {
                    visible: !manager.busy || base.openProposal !== ""
                    label: "↑"; fill: "#D6E6FB"; fg: base.blue; tip: "Отправить (Enter)"
                    onClicked: base.send()
                }
                RoundButton {
                    visible: manager.busy && base.openProposal === ""
                    label: "■"; fill: "#FDE7E5"; fg: base.danger; tip: "Остановить"
                    onClicked: manager.cancel()
                }
            }
        }
    }

    FileDialog {
        id: fileDialog
        title: "Приложить файлы"
        selectMultiple: true
        nameFilters: ["Модели, фото и ТЗ (*.stl *.obj *.3mf *.png *.jpg *.jpeg *.webp *.docx *.txt *.md)", "Все файлы (*)"]
        onAccepted: manager.addFiles(fileDialog.fileUrls)
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
            Label { text: settings.status; color: settings.statusOk ? "#1E7A4C" : base.danger; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout {
                Button { text: "Проверить подключение"; onClicked: { settings.save(); settings.status = "Проверяю…"; manager.testConnection() } }
                Item { Layout.fillWidth: true }
                Button { text: "Сохранить"; isDefault: true; onClicked: settings.save() }
                Button { text: "Закрыть"; onClicked: settings.hide() }
            }
        }
    }
}
