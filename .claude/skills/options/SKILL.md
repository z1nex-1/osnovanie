---
name: options
description: Разные варианты, возражения, расчёт. Этап options, точка G3.
---

# Варианты

1. Составь от трёх до пяти вариантов: обязательно `keep` (оставить как есть) и `close` (закрыть), плюс содержательно разные `change`. Отличие в логике ценности (кому, за что платят, против кого), а не в цвете. Методики: `methods/ost.md`, `methods/cagan.md`, `methods/competitive-pricing.md`.
2. «Оставить как есть» описывается честно: что будет через 6–12 месяцев без действий, по тем же данным.
3. Запиши каждый: `bin/osnovanie options add --file outputs/tmp/o1.json`, поля `id,title,kind,thesis,basis[],loses_if,what_changes`. В `thesis` назови главный риск по Кейгану.
4. Вызови `skeptic` и `closure-advocate`. Возражения записывают они. Сильные причины провала дай пре-мортемом (`methods/pre-mortem.md`).
5. Построй модель `outputs/tmp/model.json` по `methods/unit-economics.md`: `params` с `claim`, `low`, `base`, `high`, `unit`; `options` с формулами; `output_unit`. `bin/osnovanie model set --file ...`, `bin/osnovanie model run`.
6. В тексте для человека показывай диапазон, долю допущений и условия смены лидера, а не только среднее. Запиши `outputs/options.md`: таблица вариантов, возражения, модель, «что изменит решение».
7. Открой G3 и остановись. Рекомендацию можно назвать, решение принимает человек.
8. После `G3 да O2 O3`: `bin/osnovanie advance`.
