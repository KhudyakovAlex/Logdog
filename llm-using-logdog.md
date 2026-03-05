# LLM (client project) — using Logdog

Скопируй этот файл в проект, который будет писать логи в Logdog, чтобы Cursor/агенты могли быстро подтягивать отладку.

## 0) Preconditions (перед стартом)

- Logdog ingest **запущен** на машине в локалке и доступен по сети.
- Выбери `LOGDOG_HOST`:
  - если проект и Logdog на одной машине: `localhost`
  - если с другой машины в LAN: IP хоста Logdog (например `192.168.1.10`)
- Если Windows Firewall блокирует порт: открой входящие на `TCP 3000`.

## 1) Куда писать логи (HTTP)

Logdog ingest слушает:

- `POST http://<LOGDOG_HOST>:3000/logs`
- `Content-Type: application/json`

Минимальный JSON:

```json
{
  "level": "debug",
  "app": "my-app",
  "message": "something happened"
}
```

Рекомендуемые поля:

- `ts`: epoch ms (если не указать — проставит сервер)
- `traceId`: строка корреляции (одна на запрос/операцию/пайплайн)
- `fields`: объект с полезным контекстом (ids, параметры, метрики)

Ограничения:

- размер одной записи: до **256KB**
- уровни: `debug|info|warn|error`

Ожидаемые ответы:

- `201 Created`: вернёт JSON с `id` и нормализованным `ts`
- `400`: плохой JSON
- `413`: payload слишком большой
- `422`: не прошла валидация полей

## 2) Как договориться об идентификации (важно)

Чтобы запросы в MCP были точными, в проекте **зафиксируй**:

- `app`: стабильное имя сервиса/приложения (например `billing-api`, `desktop-ui`)
- `traceId`: формат (например UUID) и где он берётся/прокидывается

## 3) Как Cursor будет читать логи (MCP)

Cursor подключается к MCP через **stdio**. В каждом проекте-клиенте добавь `.cursor/mcp.json`
(пример можно взять из Logdog: `.cursor/mcp.json.example`), чтобы Cursor мог запускать MCP-процесс.

Варианты подключения:

- **Per-project**: `.cursor/mcp.json` в каждом проекте (самый предсказуемый).
- **Global**: `~/.cursor/mcp.json` (один раз на всю машину) — удобно, если Logdog стоит всегда в одном месте.

Пример (Windows, если Logdog находится в `D:\\Git\\Logdog` и venv уже создан):

```json
{
  "mcpServers": {
    "logdog": {
      "command": "D:\\\\Git\\\\Logdog\\\\.venv\\\\Scripts\\\\python.exe",
      "args": ["-m", "logdog.mcp_server"],
      "env": { "LOGDOG_DB_PATH": "D:\\\\Git\\\\Logdog\\\\data\\\\logdog.db" }
    }
  }
}
```

После изменения `mcp.json` **перезапусти Cursor полностью**.

## 4) Как просить LLM/агента искать нужное

Используй MCP tools:

- `recent(limit, app?, level?)` — последние записи
- `query(app?, level?, since?, until?, contains?, traceId?, limit)` — поиск по фильтрам

Шаблоны запросов:

- “Найди ошибки за последние 10 минут по `app=my-app` (level=error)”
- “Покажи лог-цепочку по `traceId=<...>`”
- “Найди записи, где `message` содержит `timeout` после момента \(ts=...\)”

Пример формулировки прямо в чате Cursor:

- `Вызови MCP tool logdog.recent с аргументами {"limit":5,"app":"my-app"}`
- `Вызови MCP tool logdog.query с аргументами {"traceId":"<id>","limit":200}`

## 5) Быстрая проверка отправки (PowerShell)

```powershell
$body = @{ level="debug"; app="my-app"; message="hello from client" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://<LOGDOG_HOST>:3000/logs" -ContentType "application/json" -Body $body
```

