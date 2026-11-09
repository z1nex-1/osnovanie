---
name: data-analysis
description: Профиль таблиц и воспроизводимые расчёты.
---

# Анализ данных

1. `bin/osnovanie data profile s003`: пропуски, дубли, период, смешанные единицы, выбросы. Проблемы называй в тексте.
2. JSON из API разверни: `bin/osnovanie data flatten s004 --file spec.json`, поля `path, fields, title`.
3. Расчёт только через команду: `bin/osnovanie data calc --file ...`, поля `id, source, op (count|sum|mean|median|min|max|share|group_count|group_mean), column, by, where, match, text, unit, period`. Результат станет утверждением типа calculation с хешем данных.
4. Качественный анализ: тематическое кодирование. Сначала темы по первым 20% записей, затем подсчёт через `data calc` c фильтром `contains`.
