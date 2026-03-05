# Logdog

Локальный сборщик отладочных логов:

- HTTP ingest в Docker на `:3000`, принимает JSON и пишет в SQLite
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
- `LOGDOG_HTTP_MAX_BYTES` (default: `262144`)
- `LOGDOG_DB_MAX_BYTES` (default: `1073741824` = 1GB)

