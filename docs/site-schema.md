# Пакет для сайта: схема `osnovanie-site/1`

`bin/osnovanie export site --out DIR` читает проект и пишет один файл `DIR/project.json`. Код — `tools/osn/site.py`. Файл самодостаточен: сайт не обращается к состоянию проекта. Ничего не записывается в сам проект.

## Верхний уровень

| Ключ | Что внутри |
|---|---|
| `schema` | строка `osnovanie-site/1` |
| `generated_at` | время сборки, ISO с часовым поясом |
| `project` | `slug`, `title`, `question`, `reference` (эталонный проект), `note`, `stage`, `stage_label`, `decision` (вариант, ответственный, время), `created_at` |
| `brief` | постановка: вопрос, цель, получатель, ограничения, данные |
| `claims` | утверждения: `id`, `type`, `effective_type`, `status`, `confidence`, `text`, `quote`, `source_id`, `source` (название, адрес, домен, дата публикации), `value`, `unit`, `topic`, `range`, `created_at`, `updated_at`, `expires`, `depends_on`, `superseded_by`, `supersedes`, `verify_plan`, `resolution`, `conflict_between`, `calc_id`, `author`, `stale_reason` |
| `edges` | рёбра графа `{from, to, kind}`; `kind`: `depends_on` (утверждение от утверждения), `cites` (источник к утверждению), `basis` (утверждение к варианту), `objection` (утверждение, на которое опирается возражение, к варианту) |
| `doc_refs` | ссылки `[cNNN]` в документах: `{doc, paragraph, claim}` |
| `sources` | `id`, `title`, `kind`, `origin`, `domain`, `sha256`, `published_at`, `accessed_at`, `offline`, `chars`, `quarantined`, `status` |
| `options` | варианты: тезис, опоры (`basis`), условия проигрыша, `portfolio`, возражения с авторами и опорными утверждениями |
| `model` | см. ниже; `null`, если модели нет |
| `questions` | вопросы со статусами (`open`, `answered`, `gap`) |
| `human_points` | точки G1-G4 и R: статус, канал (`channel`), `by_human` (чат и терминал считаются человеком, сценарий нет), время открытия и закрытия, комментарий |
| `events` | события журнала со временем `ts` и видом `kind` |
| `reviews` | пересмотры: что заменено, что затронуто, лидер до и после, `waves` — слои волны по графу (`depth`, `claims`) |
| `commitments` | обязательства: что, кто, срок, показатель, порог, `check_at` |
| `cost` | стоимость: LLM, человек, по этапам и моделям |
| `score` | индекс обоснованности с разбивкой (`docs/score.md`) |
| `layout` | раскладка графа |

## `model`

`output_unit`, `samples` (число прогонов Монте-Карло), `formulas`, `constants`, `params` (диапазоны величин последнего расчёта с опорами), `ranking`, `assumption_share`, `run` (номер расчёта), `breakeven` (точки перелома: величина, порог, новый лидер), `history` (все расчёты), `monte_carlo`: для каждого варианта `base`, `p10`, `p90`, `p_best` и `histogram` — `edges` (на одну больше, чем столбцов) и `counts` (24 столбца).

## `layout`

`algorithm` (`fruchterman-reingold-3d`), `seed` и `nodes`: `{id, kind, x, y, z}`, где `kind` — `claim`, `source` или `option`, а координаты лежат в `[-1, 1]`. Раскладка считается на чистом Python при каждом экспорте и детерминирована: один проект даёт одни координаты.

## Совместимость

Новые ключи добавляются без смены версии. Смена смысла или удаление ключа меняют номер схемы.
