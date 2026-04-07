# Logdog

Локальный сборщик отладочных логов:

- HTTP ingest в Docker на `:3000`, принимает JSON и пишет в SQLite
- В лог-запись можно вкладывать большие текстовые блоки `md/json` и скриншоты `image` как attachments
- Встроенная UI-страница `/ui/` показывает логи, текстовые вложения и превью скриншотов
- Cursor AI читает логи через MCP (stdio) из той же SQLite БД

## Быстрый старт (ingest в Docker)

1) Создай папку для данных (на хосте):

```bash
mkdir data
```

2) Запусти ingest:

```bash
docker compose up --build
```

3) Отправь тестовую запись:

```bash
curl -X POST http://localhost:3000/logs ^
  -H "Content-Type: application/json" ^
  -d "{\"level\":\"debug\",\"app\":\"demo\",\"message\":\"hello\"}"
```

Пример с вложением:

```bash
curl -X POST http://localhost:3000/logs ^
  -H "Content-Type: application/json" ^
  -d "{\"level\":\"info\",\"app\":\"demo\",\"message\":\"saved request body\",\"attachments\":[{\"kind\":\"json\",\"name\":\"request.json\",\"content\":\"{\\\"ok\\\":true,\\\"items\\\":[1,2,3]}\"}]}"
```

Пример со скриншотом:

```bash
curl -X POST http://localhost:3000/logs ^
  -H "Content-Type: application/json" ^
  -d "{\"level\":\"info\",\"app\":\"demo\",\"message\":\"user sent screenshot\",\"attachments\":[{\"kind\":\"image\",\"name\":\"screen.jpg\",\"mime\":\"image/jpeg\",\"width\":1080,\"height\":2400,\"contentBase64\":\"<BASE64>\"}]}"
```

## MCP для Cursor (stdio)

MCP запускается **локально** (не в Docker) и читает `./data/logdog.db`.

1) Установи зависимости:

```bash
python -m venv .venv
.venv\\Scripts\\pip install -r requirements.txt
```

2) Пример ручного запуска (для проверки): Cursor будет запускать аналогично.

```bash
set LOGDOG_DB_PATH=.\data\logdog.db
.venv\\Scripts\\python -m logdog.mcp_server
```

### Подключение MCP в Cursor

1) Скопируй пример конфига:

```bash
mkdir .cursor
copy .cursor\\mcp.json.example .cursor\\mcp.json
```

2) Перезапусти Cursor полностью.

## Конфигурация (env)

- `LOGDOG_DB_PATH` (default: `./data/logdog.db`)
- `LOGDOG_BLOB_DIR` (default: `./data/blobs`)
- `LOGDOG_HTTP_MAX_BYTES` (default: `4194304` = 4MB)
- `LOGDOG_DB_MAX_BYTES` (default: `1073741824` = 1GB)

## Вложения `md/json/image`

- `POST /logs` принимает `attachments`
- поддержаны `kind: "md"`, `kind: "json"` и `kind: "image"`
- для `image` нужно передавать `mime`, `contentBase64`, опционально `width`, `height`
- изображения хранятся на диске, а в SQLite лежат только их метаданные
- в `/api/recent` и `/api/query` возвращаются только метаданные вложений
- текстовое содержимое доступно через `GET /api/attachments/{id}`
- бинарный файл картинки доступен через `GET /api/attachments/{id}/file`
- UI на `/ui/` показывает вложения ссылками и открывает:
  - `json` как свернутое дерево
  - `md` как отформатированный документ по секциям
  - `image` как preview/full-size просмотр

## MCP tools

- `recent(limit, app?, level?)`
- `query(app?, level?, since?, until?, contains?, traceId?, limit?)`
- `attachment(id)` - вернуть содержимое вложения
- `image_attachment(id)` - вернуть картинку вложения для Cursor/агента

