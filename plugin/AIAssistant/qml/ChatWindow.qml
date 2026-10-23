// The chat inside the slicer. Data: manager.eventAdded(json) / manager.cleared()
// stream the session's events (the same ones the browser page polls, see
// slicer_chat.py); actions are manager.send / addFiles / cancel / ... .
// Designers restyle this file; the manager calls are the contract.
import QtQuick 2.2
import QtQuick.Controls 1.4
import QtQuick.Dialogs 1.2
import QtQuick.Layouts 1.1
import QtQuick.Window 2.2

Window {
    id: base
    title: "AI Assistant — STE Slicer"
    width: 760
    height: 820
    minimumWidth: 480
    minimumHeight: 400
    color: palette.base

    SystemPalette { id: palette }

    property var callRows: ({})
    property var proposalRows: ({})
    property string openProposal: ""
    property string thinking: ""
    property real progress: -1
    property var attachments: JSON.parse(manager.attachmentsJson)

    function addRow(row) {
        messages.append(row);
        list.positionViewAtEnd();
        return messages.count - 1;
    }

    function onEvent(ev) {
        var d = ev.data || {};
        if (ev.type !== "thinking") thinking = "";
        switch (ev.type) {
        case "user":
            var names = (d.attachments || []).map(function (a) { return a.name; }).join(", ");
            addRow({ kind: "user", text: d.text || "", extra: names, summary: "", details: "" }); break;
        case "assistant":
            addRow({ kind: "assistant", text: d.html || "", extra: "", summary: "", details: "" }); break;
        case "note": case "status":
            addRow({ kind: "note", text: d.text || "", extra: "", summary: "", details: "" }); break;
        case "error":
            addRow({ kind: "error", text: d.text || "", extra: "", summary: "", details: "" }); break;
        case "thinking":
            thinking = "Ассистент думает… шаг " + d.step + (d.tokens ? " · потрачено " + d.tokens + " токенов" : ""); break;
        case "tool_call":
            callRows[d.id] = addRow({ kind: "tool", text: d.name + "(" + (d.args || "") + ")", extra: "", summary: "…", details: "" }); break;
        case "tool_result":
            var i = callRows[d.id];
            if (i !== undefined) {
                messages.setProperty(i, "summary", "→ " + d.summary + " (" + d.ms + " мс)");
                messages.setProperty(i, "details", d.details || "");
                messages.setProperty(i, "extra", d.ok ? "ok" : "fail");
            }
            break;
        case "progress":
            progress = d.result ? -1 : (d.value || 0); break;
        case "proposal":
            openProposal = d.id;
            proposalRows[d.id] = addRow({ kind: "proposal", text: "Ассистент предлагает изменения: " + d.items.length + " пункт(ов). Проверьте их в карточке.", extra: "open", summary: "", details: "" });
            break;
        case "proposal_closed":
            if (openProposal === d.id) openProposal = "";
            var p = proposalRows[d.id];
            if (p !== undefined) {
                messages.setProperty(p, "extra", "closed");
                messages.setProperty(p, "summary", { apply: "Применено", revise: "Отправлено на уточнение", reject: "Отклонено", cancel: "Остановлено" }[d.action] || d.action);
            }
            break;
        case "usage":
            addRow({ kind: "usage", text: d.line || "", extra: "", summary: "", details: "" }); break;
        case "idle":
            progress = -1; break;
        }
    }

    Connections {
        target: manager
        onEventAdded: base.onEvent(JSON.parse(json))
        onCleared: { messages.clear(); callRows = {}; proposalRows = {}; openProposal = ""; thinking = ""; progress = -1 }
        onConfigTested: { settings.status = text; settings.statusOk = ok }
        onVoiceText: { input.text = (input.text.trim() ? input.text.trim() + " " : "") + text; input.forceActiveFocus() }
    }

    ListModel { id: messages }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Label { text: "AI Assistant"; font.bold: true; font.pointSize: 13 }
            Label { text: manager.busy ? "работает" : "готов"; color: manager.busy ? "#1f4fd1" : "#1e7a4c" }
            Item { Layout.fillWidth: true }
            Button { text: "Новый диалог"; onClicked: manager.newConversation() }
            Button { text: "В браузере"; tooltip: "Тот же диалог в браузере"; onClicked: manager.openInBrowser() }
            Button { text: "Настройки"; onClicked: settings.open() }
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            ListView {
                id: list
                model: messages
                spacing: 8
                delegate: Column {
                    id: rowItem
                    width: list.width - 16
                    property bool expanded: false

                    Item {  // user
                        visible: kind === "user"
                        width: parent.width
                        height: visible ? bubble.height : 0
                        Rectangle {
                            id: bubble
                            anchors.right: parent.right
                            width: Math.min(parent.width * 0.85, Math.max(userText.implicitWidth, att.implicitWidth) + 24)
                            height: userText.height + (att.visible ? att.height : 0) + 16
                            radius: 10
                            color: "#e3eafb"
                            Text { id: userText; x: 12; y: 8; width: parent.width - 24; text: model.text; wrapMode: Text.Wrap; color: "#16212c" }
                            Text { id: att; anchors.top: userText.bottom; x: 12; text: "Вложения: " + model.extra; visible: model.extra !== ""; color: "#1f4fd1"; font.pointSize: 9 }
                        }
                    }
                    Text {  // assistant answer (rich text prepared in slicer_chat.markdown_html)
                        visible: kind === "assistant"
                        width: parent.width
                        text: kind === "assistant" ? model.text : ""
                        textFormat: Text.RichText
                        wrapMode: Text.Wrap
                        color: palette.text
                    }
                    Text {  // note, status, error, usage
                        visible: kind === "note" || kind === "error" || kind === "usage"
                        width: parent.width
                        text: visible ? model.text : ""
                        wrapMode: Text.Wrap
                        font.italic: kind === "note"
                        font.pointSize: kind === "usage" ? 9 : 11
                        color: kind === "error" ? "#b42318" : palette.dark
                    }
                    Text {  // tool call, click for the raw result
                        visible: kind === "tool"
                        width: parent.width
                        text: (rowItem.expanded ? "▾ " : "▸ ") + model.text + "  " + model.summary
                        elide: Text.ElideRight
                        font.family: "monospace"
                        font.pointSize: 9
                        color: model.extra === "fail" ? "#b42318" : palette.dark
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: rowItem.expanded = !rowItem.expanded }
                    }
                    TextArea {
                        visible: kind === "tool" && rowItem.expanded && model.details !== ""
                        width: parent.width
                        height: visible ? 160 : 0
                        readOnly: true
                        text: visible ? model.details : ""
                        font.family: "monospace"
                    }
                    Rectangle {  // plan card placeholder in the chat; the card itself is PlanCard.qml
                        visible: kind === "proposal"
                        width: parent.width
                        height: visible ? pr.implicitHeight + 16 : 0
                        radius: 8
                        border.color: model.extra === "open" ? "#1f4fd1" : palette.mid
                        color: "transparent"
                        RowLayout {
                            id: pr
                            anchors.fill: parent
                            anchors.margins: 8
                            Text { text: model.text + (model.summary ? " — " + model.summary : ""); wrapMode: Text.Wrap; Layout.fillWidth: true; color: palette.text }
                            Button { text: "Показать карточку"; visible: model.extra === "open"; onClicked: manager.showCard() }
                        }
                    }
                }
                footer: Label {
                    text: base.thinking
                    visible: base.thinking !== ""
                    color: palette.dark
                    font.italic: true
                }
            }
        }

        ProgressBar { Layout.fillWidth: true; visible: base.progress >= 0; value: Math.max(0, base.progress) }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            visible: base.attachments.length > 0
            Repeater {
                model: base.attachments
                delegate: Button { text: modelData.name + "  ×"; tooltip: "Убрать"; onClicked: manager.removeAttachment(modelData.id) }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Button { text: "+"; tooltip: "Приложить STL/OBJ/3MF, фото или ТЗ"; Layout.preferredWidth: 36; onClicked: fileDialog.open() }
            Button {
                visible: manager.voiceAvailable
                text: manager.voiceState === "recording" ? "● Стоп" : manager.voiceState === "transcribing" ? "…" : "Голос"
                tooltip: "Нажмите и говорите, нажмите ещё раз — текст появится в поле (Esc — отмена)"
                enabled: manager.voiceState !== "transcribing"
                onClicked: manager.toggleVoice()
            }
            TextArea {
                id: input
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                wrapMode: TextEdit.Wrap
                Keys.onReturnPressed: {
                    if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return; }
                    base.send(); event.accepted = true;
                }
                Keys.onEnterPressed: { base.send(); event.accepted = true }
                Keys.onEscapePressed: manager.cancelVoice()
            }
            ColumnLayout {
                Button { text: "Отправить"; visible: !manager.busy || base.openProposal !== ""; onClicked: base.send() }
                Button { text: "Стоп"; visible: manager.busy; onClicked: manager.cancel() }
            }
        }
        Label {
            text: base.openProposal !== "" ? "Ассистент ждёт решения по карточке. Сообщение отсюда уйдёт как уточнение." : "Enter — отправить, Shift+Enter — новая строка"
            color: palette.dark
            font.pointSize: 9
        }
    }

    function send() {
        if (input.text.trim() === "" && base.attachments.length === 0) return;
        manager.send(input.text);
        input.text = "";
    }

    FileDialog {
        id: fileDialog
        title: "Приложить файлы"
        selectMultiple: true
        nameFilters: ["Модели, фото и ТЗ (*.stl *.obj *.3mf *.png *.jpg *.jpeg *.webp *.docx *.txt *.md)", "Все файлы (*)"]
        onAccepted: manager.addFiles(fileDialog.fileUrls)
    }

    // ---- settings
    Window {
        id: settings
        title: "Настройки AI Assistant"
        width: 520
        height: form.implicitHeight + 32
        flags: Qt.Dialog
        color: palette.window
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
            CheckBox { id: popups; text: "Всплывающая карточка и уведомления в слайсере" }
            Label { text: settings.status; color: settings.statusOk ? "#1e7a4c" : "#b42318"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout {
                Button { text: "Проверить подключение"; onClicked: { settings.save(); settings.status = "Проверяю…"; manager.testConnection() } }
                Item { Layout.fillWidth: true }
                Button { text: "Сохранить"; isDefault: true; onClicked: settings.save() }
                Button { text: "Закрыть"; onClicked: settings.hide() }
            }
        }
    }
}
