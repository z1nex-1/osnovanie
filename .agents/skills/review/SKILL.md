---
name: review
description: Пересмотр выводов при новых данных. Этап decided.
---

# Пересмотр

1. Загрузи новый источник, выжимка через `source-sanitizer`.
2. Если данные меняют прежнее утверждение: `bin/osnovanie claim supersede c012 --file ...` со ссылкой на новый источник. Старое остаётся в журнале.
3. Запусти `bin/osnovanie verify`, посмотри `bin/osnovanie review preview`.
4. `bin/osnovanie review run --note "что случилось"`. Отчёт в `outputs/review-N.md`, откроется точка R. Остановись.
5. После `R да` перепиши затронутые абзацы и замени ссылки. Если сменился лидер, этап вернётся к стратегиям и потребуется новая точка G4.
6. `bin/osnovanie review finish`, `bin/osnovanie map`. Покажи `bin/osnovanie review diff`.

Что решать сейчас: `bin/osnovanie decide next`.
