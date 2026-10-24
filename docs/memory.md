# Память правок агента: бэкенд

Модуль `plugin/AIAssistant/agent/memory.py`, файл `ai_assistant/memory.json` в папке данных слайсера
(у каждого ПК свой). Формат — контракт из плана v0.7.0: `events`, `rules`, `cases`, плюс поле `role`.

## Как правка становится правилом

| Что сделал пользователь | Что сохраняется |
|---|---|
| Поправил значение в карточке | Событие `edit` в журнале. Правила нет |
| Та же правка (ключ, значение, материал) второй раз без «Запомнить» | Правило-кандидат на материал. Агент видит его как подсказку, код не применяет. Включает эксперт (`update_rule(id, status="active")`) |
| «Запомнить» → эта деталь | Активное правило `part` (scope_value = mesh_hash), любой роли |
| «Запомнить» → материал / режим / всегда | Активное правило, только для эксперта. Новичку — отказ с причиной |
| Правка режима 3D/5D | Прецедент в `cases`, не правило. Для той же сетки режим ставится сам, похожие детали идут в подсказку |
| Значение не прошло `validate` | Правило не сохраняется, причина в ответе и в `note` события |

Приоритет правил на один ключ: деталь > материал > режим > всегда, при равенстве побеждает более свежее.

## validate(key, value, context)

- Стек слайсера: `minimum_value` / `maximum_value` отклоняют всегда; `*_warning` отклоняют новичка, а эксперту
  дают только примечание. Неизвестный ключ и вариант enum не из списка — отказ.
- База знаний: обдув PC/ABS/PA ≤ 20 %, PP G30 ≤ 10 % (20_settings.md); raft в 5D запрещён (уточнить у печатника);
  слой ≤ 0.3 мм; сопло выше `heat_c` материала, стол не выше `heat_c` + 30 (materials.csv); материал должен быть
  в materials.csv и не special. Режим правилом не бывает.

## Карточка (session.py)

- `proposal` event: у строки из правила есть `from_memory` = `{item_id, rule_id, from, to, scope, scope_value, kind}`
  (`kind`: `rule` или `case`), плюс общий список `applied_rules` и `role`.
- `answer_proposal(id, action, overrides, skip, text, remember)`, где `remember = {"keys": [item id], "scope": ...}`.
  HTTP: то же поле `remember` в `POST /api/proposal`.
- `check_remember(proposal_id, item_id, value, scope)` → `{ok, reason}` — для красной подсказки до «Применить».
- После применения приходит event `memory_saved`: `{events, rules: {key: {ok, status, rule_id, reason}}, cases, note}`.

## Окно памяти

Из QML-моста напрямую: `memory.get().list_rules(filter, sort)`, `list_events(filter, sort)`, `update_rule`,
`disable_rule`, `delete_rule`, `rollback(to_ts)`, `export()`, `import_(data, merge=True)`, `role()`, `set_role()`.
Ошибки — `memory.MemoryRefused` с текстом для пользователя. Из браузера: `POST /api/memory`
`{"method": "list_rules", "args": {...}}`.

Фильтры: `from_ts`, `to_ts`, `material`, `mode`, `key`, `status`, `role`, `scope`, `action`, `part`
(имя или mesh_hash). Сортировка: имя поля, `-` впереди — по убыванию (`-ts`, `key`, `material`, `status`).
Откат, импорт и правила шире детали — только эксперт.

## Для LLM

- `get_memory(object_id?, material_id?, mode?)` — только чтение.
- Блок «Опыт этого ПК» (≤ 15 строк) сам приезжает в результате `analyze_model` / `recommend_print_mode`.
- В результате `apply_plan` — `memory_applied` и `user_remembered`.
- Признаки детали для прецедентов берутся из результата инструмента, где есть `object_id` и `features`
  (и `class`), то есть из `recommend_print_mode` задачи 1.

Тесты: `python -m unittest discover -s tests`.
