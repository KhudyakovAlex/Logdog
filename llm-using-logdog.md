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
- `attachments`: большие текстовые блоки `md/json` и скриншоты `image`, которые нужно сохранить отдельно от основного сообщения

Пример `attachments`:

```json
[
  {
    "kind": "json",
    "name": "request-body.json",
    "content": "{\n  \"userId\": 42,\n  \"flags\": [\"demo\"]\n}"
  },
  {
    "kind": "md",
    "name": "prompt.md",
    "content": "# Prompt\n\n- step 1\n- step 2\n"
  }
]
```

Пример image attachment:

```json
[
  {
    "kind": "image",
    "name": "checkout-screen.jpg",
    "mime": "image/jpeg",
    "width": 1080,
    "height": 2400,
    "contentBase64": "<BASE64>"
  }
]
```

Важно:

- `attachments[].content` для `kind: "json"` должен быть **валидным JSON-текстом**
- `attachments[].content` для `kind: "md"` передаётся как обычный markdown-текст
- `attachments[].contentBase64` для `kind: "image"` должен быть base64-строкой картинки
- для `kind: "image"` поддержаны `mime`: `image/jpeg`, `image/png`, `image/webp`
- в одном событии можно передавать несколько вложений
- при отправке скриншотов лучше заранее уменьшать размер картинки, чтобы не выйти за лимит 4MB

Ограничения:

- размер одной записи: до **4MB**
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
- `query(app?, level?, since?, until?, contains?, traceId?, limit)` — поиск по фильтрам, включая `message`, имя вложения и текст вложения
- `attachment(id)` — получить полное содержимое вложения
- `image_attachment(id)` — получить картинку вложения

Важно:

- `recent(...)` и `query(...)` возвращают у вложений только метаданные (`id`, `kind`, `name`, `sizeBytes`)
- чтобы получить полный текст `md/json`, вызывай `attachment(id)`
- чтобы получить картинку, вызывай `image_attachment(id)`

Шаблоны запросов:

- “Найди ошибки за последние 10 минут по `app=my-app` (level=error)”
- “Покажи лог-цепочку по `traceId=<...>`”
- “Найди записи, где `message` содержит `timeout` после момента \(ts=...\)”

Пример формулировки прямо в чате Cursor:

- `Вызови MCP tool logdog.recent с аргументами {"limit":5,"app":"my-app"}`
- `Вызови MCP tool logdog.query с аргументами {"traceId":"<id>","limit":200}`
- `Вызови MCP tool logdog.attachment с аргументами {"id":123}`
- `Вызови MCP tool logdog.image_attachment с аргументами {"id":456}`

## 5) Быстрая проверка через UI

- Открой `http://<LOGDOG_HOST>:3000/ui/`
- В списке логов вложения отображаются как ссылки
- По клику:
  - `json` открывается как отформатированное дерево со сворачиванием
  - `md` открывается как отформатированный документ по секциям
  - `image` открывается как preview/full-size просмотр

## 6) Android: отправка текущего экрана

- В репозитории Logdog есть готовый пример файла: `LogdogScreenshotSender.kt`
- Файл автономный: снимает текущий `Activity`, сжимает в JPEG, кодирует в base64 и шлёт в `POST /logs`
- Его нужно встроить в уже существующее Android-приложение и вызывать из нужного места UI

Рекомендуемые поля для скриншота:

- `message`: например `user sent screenshot`
- `traceId`: текущая корреляция запроса/сценария
- `fields.screen`: стабильное имя экрана
- `fields.deviceModel`, `fields.androidVersion`, `fields.appVersion`: полезный технический контекст

## 7) Быстрая проверка отправки (PowerShell)

```powershell
$body = @{ level="debug"; app="my-app"; message="hello from client" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://<LOGDOG_HOST>:3000/logs" -ContentType "application/json" -Body $body
```

Пример с JSON-вложением:

```powershell
$payload = @{
  level = "info"
  app = "my-app"
  message = "captured request body"
  attachments = @(
    @{
      kind = "json"
      name = "request-body.json"
      content = "{`n  `"userId`": 42,`n  `"ok`": true`n}"
    }
  )
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "http://<LOGDOG_HOST>:3000/logs" -ContentType "application/json" -Body $payload
```

Пример со скриншотом:

```powershell
$payload = @{
  level = "info"
  app = "my-app"
  message = "user sent screenshot"
  traceId = "demo-trace"
  fields = @{
    screen = "CheckoutScreen"
    device = "Pixel 7"
  }
  attachments = @(
    @{
      kind = "image"
      name = "checkout-screen.jpg"
      mime = "image/jpeg"
      width = 1080
      height = 2400
      contentBase64 = "<BASE64>"
    }
  )
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri "http://<LOGDOG_HOST>:3000/logs" -ContentType "application/json" -Body $payload
```

