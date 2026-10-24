# AI Assistant для STE Slicer

Плагин добавляет в STE Slicer чат-ассистента на базе LLM. Пользователь описывает задачу (или прикладывает ТЗ в .docx),
3D-модель и фото, а ассистент сам выбирает режим печати (3D / 5D Spiral / 5D), материал, профиль и настройки,
ориентирует модель, нарезает её и сохраняет `project.3mf` и `result.gcode`, объясняя каждое решение.

## Установка

1. Закройте STE Slicer.
2. Скопируйте папку `AIAssistant` в папку плагинов пользователя:
   `%APPDATA%\steslicer\7.x\plugins\` (открыть: **Справка → Показать папку конфигурации**, внутри папка `plugins`;
   если её нет — создайте). Должно получиться `...\plugins\AIAssistant\plugin.json`.
3. Запустите слайсер. В меню **Extensions** появится **AI Assistant → Open chat**.

## Настройка LLM

1. **Extensions → AI Assistant → Open chat** — чат откроется в браузере (`http://127.0.0.1:<порт>/`).
2. При первом запуске откроется окно настроек: укажите **URL** (OpenAI-совместимый, например `https://host/v1`),
   **ключ** и **модель**. Кнопка «Проверить подключение» покажет доступные модели.
3. Ключ хранится в настройках приложения (`steslicer.cfg`), а не в коде. В браузер он не возвращается.

## Использование

- Напишите задачу и прикрепите файлы (кнопка «＋» или перетаскивание): STL/OBJ/3MF, PNG/JPG, DOCX/TXT.
- В чате видно каждый вызов инструмента и его результат (клик раскрывает подробности), прогресс нарезки,
  итог: время печати и расход материала.
- Результаты сохраняются в `Документы\STE AI Assistant\results\<деталь>\` (`project.3mf`, `result.gcode`).
  Папку можно поменять в настройках.

## Голосовой ввод

- Кнопка с микрофоном рядом с полем ввода: нажать и говорить, нажать ещё раз — текст появится в поле
  (Esc — отмена). В настройках можно включить автоотправку.
- На Windows звук записывает сам плагин (`voice.py`, winmm через ctypes), без разрешений браузера.
  Речь распознаёт модель `…-transcribe` / whisper через тот же шлюз и ключ (`/audio/transcriptions`);
  модель выбирается автоматически из списка шлюза или задаётся в настройках.
- Запасные варианты: запись в браузере (MediaRecorder) и Web Speech API (Chrome/Edge).
- API для любого окна чата: `POST /api/voice/start`, `/api/voice/stop` (→ `{text}`), `/api/voice/cancel`;
  из Python — `voice.start()`, `voice.stop()` (WAV), `voice.transcribe(audio, mime)`, `voice.is_available()`.

## Архитектура

```
web/ (SPA: HTML+CSS+JS)  ──fetch / polling──▶  server.py (HTTP на 127.0.0.1)
                                                   │
                                     agent/session.py — цикл диалога с LLM
                                       │                       │
                         agent/mcp.py — MCP-сервер     agent/llm.py — Chat Completions + tools
                         (initialize, tools/list,
                          tools/call; POST /mcp)
                                       │
                         agent/tools.py — инструменты поверх API слайсера
                         (вызовы в главном Qt-потоке через agent/mainthread.py)
```

- `knowledge/*.md` — правила подбора, подмешиваются в системный промпт. Их можно править без кода.
- Описания материалов берутся из самого слайсера (`list_materials`), а свойства для подбора — из
  `knowledge/materials.csv` (разделитель `;`, можно править в Excel). `recommend_materials` сверяет
  с ней требования ТЗ и ранжирует материалы.

### Инструменты

| Группа | Инструменты |
|---|---|
| Обзор | `get_scene_info` |
| Режим | `set_print_mode` (classic / cylindrical / cylindrical_full) |
| Профили и материалы | `list_quality_profiles`, `set_quality_profile`, `list_materials`, `recommend_materials`, `set_material`, `set_object_extruder` |
| Настройки | `search_settings`, `get_setting`, `set_settings`, `reset_settings` |
| Модели | `load_model`, `list_objects`, `analyze_model`, `orient_object`, `rotate_object`, `move_object`, `scale_object`, `arrange_all`, `delete_object`, `clear_scene` |
| Нарезка и экспорт | `slice`, `get_slice_result`, `export_gcode`, `save_project_3mf`, `save_results` |
| Составные шаги | `apply_plan` (режим → материал → профиль → настройки за один вызов; в чате сначала карточка с подтверждением, см. `docs/plan-card.md`), `slice_and_save` |

Через MCP доступны все инструменты. Модели LLM показываются только 14 из 32 (`get_scene_info`, `load_model`,
`analyze_model`, `recommend_print_mode`, `recommend_materials`, `get_memory`, `list_materials`, `search_settings`, `apply_plan`, `orient_object`,
`rotate_object`, `move_object`, `clear_scene`, `slice_and_save`): описания инструментов отправляются на
каждом шаге, а составные инструменты сокращают число шагов.

### MCP

`POST http://127.0.0.1:<порт>/mcp`, JSON-RPC 2.0, заголовок `X-STE-AI: 1` обязателен.
Поддерживаются `initialize`, `tools/list`, `tools/call`, `ping`.

## Отладка

- Логи: `stderr.log` и `steslicer.log` в папке конфигурации.
- Настройки → «Скачать все настройки слайсера» — JSON со всеми ключами, значениями и вариантами.
- Настройки → «Режим разработчика» включает консоль, выполняющую Python внутри слайсера
  (переменная `app` — приложение). Только для разработки.

## Совместимость

Python 3.5 (как в дистрибутиве STE Slicer), только стандартная библиотека + PyQt5 и numpy из слайсера.
