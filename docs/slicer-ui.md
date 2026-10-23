# Интерфейс внутри слайсера

Кейс требует окно чат-бота в интерфейсе программы, поэтому основной интерфейс теперь живёт в окнах STE Slicer (QML).
Веб-страница осталась запасным вариантом: «AI Assistant → Open chat in browser». Это тот же диалог, оба окна
можно держать открытыми одновременно.

| Файл | Что это | Python-сторона |
|---|---|---|
| `plugin/AIAssistant/qml/ChatWindow.qml` | Окно чата: сообщения, вызовы инструментов, вложения, голос, настройки | `slicer_chat.py` (`manager`) |
| `plugin/AIAssistant/qml/PlanCard.qml` | Карточка изменений перед применением плана | `slicer_ui.py` (`manager`) |
| Уведомления слайсера | «Ассистент закончил», «Ошибка», с кнопкой «Открыть чат» | `slicer_ui.py`, штатный `UM.Message` |

Слайсер построен на Qt 5.10, поэтому используются QtQuick 2.2 и QtQuick.Controls 1.4.
Controls 2 и синтаксис Qt 5.15 (`function onFoo()` в `Connections`) недоступны.

## Контракт окна чата (`manager` в ChatWindow.qml)

- Сигналы: `eventAdded(json)` — одно событие сессии; `cleared()` — новый диалог; `voiceText(text)` — распознанная речь;
  `configTested(ok, text)`.
- Свойства: `busy`, `attachmentsJson`, `voiceAvailable`, `voiceState` (`""`, `"recording"`, `"transcribing"`).
- Действия: `send(text)`, `addFiles(urls)`, `removeAttachment(id)`, `cancel()`, `newConversation()`, `showCard()`,
  `openInBrowser()`, `toggleVoice()`, `cancelVoice()`, `configJson()`, `saveConfig(json)`, `testConnection()`.

События те же, что получает веб-страница. Python заранее готовит для QML поля `html` (у ответа ассистента),
`summary` и `details` (у результата инструмента), `args` (у вызова инструмента) и `line` (у расхода токенов).

Контракт карточки описан в [plan-card.md](plan-card.md). В QML это `manager.proposalJson`,
`manager.answer(action, overridesJson, skipJson, text)` и `manager.openChat()`.
