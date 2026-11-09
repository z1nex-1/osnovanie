---
name: options
description: Разные варианты, возражения, расчёт. Этап options, точка G3.
---

# Варианты

1. Составь от трёх вариантов: обязательно `keep` (оставить как есть) и `close` (закрыть), плюс содержательно разные `change`. Отличие в логике ценности, а не в цвете. Методики: `methods/ost.md`, `methods/rice.md`.
2. Запиши каждый: `bin/osnovanie options add --file outputs/tmp/o1.json`, поля `id,title,kind,thesis,basis[],loses_if,what_changes`.
3. Вызови `skeptic` и `closure-advocate`. Возражения записывают они.
4. Построй модель `outputs/tmp/model.json`: `params` с `claim`, `low`, `base`, `high`, `unit`; `options` с формулами; `output_unit`. `bin/osnovanie model set --file ...`, `bin/osnovanie model run`.
5. В тексте для человека показывай диапазон, долю допущений и условия смены лидера, а не только среднее.
6. Открой G3 и остановись. Рекомендацию можно назвать, решение принимает человек.
7. После `G3 да O2 O3`: `bin/osnovanie advance`.
