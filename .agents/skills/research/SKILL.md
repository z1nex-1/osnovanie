---
name: research
description: Сбор, чтение и проверка материалов. Этап research.
---

# Исследование

1. Загрузи источники через `researcher`, файлы пользователя через `bin/osnovanie source add`.
2. На каждый источник запусти `source-sanitizer`. Его выжимку запиши как утверждения: сохрани JSON в `outputs/tmp/` и `bin/osnovanie claim add-batch --file ...`.
3. Запусти `bin/osnovanie verify`. Утверждения со статусом contested исправь или отзови.
4. Для ключевых выводов вызови `verifier`, затем `bin/osnovanie verify --judge` по желанию.
5. Сопоставь источники. Где они расходятся, запиши оба утверждения и своё объяснение как hypothesis.
6. Таблицы считай через навык `data-analysis`. В уме не считай.
7. Закрой каждый вопрос: `question answer q001 --claims c1,c2` или `question gap q001 --why "почему не узнали"`.
8. Напиши `outputs/situation.md`: разделы «Что известно», «Откуда», «Чего мы не знаем». Каждое число со ссылкой.
9. `bin/osnovanie check`, затем `bin/osnovanie advance`.
