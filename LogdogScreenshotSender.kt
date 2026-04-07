package logdog

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlin.math.roundToInt

object LogdogScreenshotSender {
    data class Config(
        val serverUrl: String,
        val appName: String,
        val message: String = "user sent screenshot",
        val traceId: String? = null,
        val screenName: String? = null,
        val extraFields: Map<String, Any?> = emptyMap(),
        val jpegQuality: Int = 75,
        val maxWidth: Int = 1080,
        val connectTimeoutMs: Int = 10_000,
        val readTimeoutMs: Int = 30_000,
    )

    data class SendResult(
        val logId: Long?,
        val ts: Long?,
        val responseBody: String,
    )

    fun sendCurrentScreenAsync(
        activity: Activity,
        config: Config,
        onComplete: (Result<SendResult>) -> Unit = {},
    ) {
        thread(name = "LogdogScreenshotSender", isDaemon = true) {
            try {
                val captured = captureActivityBitmap(activity)
                val prepared = prepareBitmap(captured, config.maxWidth)
                if (prepared.bitmap !== captured) {
                    captured.recycle()
                }

                val jpegBytes = prepared.bitmap.toJpegBytes(config.jpegQuality)
                prepared.bitmap.recycle()
                val base64 = Base64.encodeToString(jpegBytes, Base64.NO_WRAP)
                val payload = buildPayload(config, prepared.width, prepared.height, base64)
                val result = postLogs(config, payload)
                dispatchResult(onComplete, Result.success(result))
            } catch (t: Throwable) {
                dispatchResult(onComplete, Result.failure(t))
            }
        }
    }

    private data class PreparedBitmap(
        val bitmap: Bitmap,
        val width: Int,
        val height: Int,
    )

    private fun captureActivityBitmap(activity: Activity): Bitmap {
        return runOnMainThreadBlocking {
            val root = activity.window?.decorView?.rootView
                ?: throw IllegalStateException("Activity does not have a root view")
            if (root.width <= 0 || root.height <= 0) {
                throw IllegalStateException("Root view is not laid out yet")
            }

            val bitmap = Bitmap.createBitmap(root.width, root.height, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bitmap)
            root.draw(canvas)
            bitmap
        }
    }

    private fun prepareBitmap(bitmap: Bitmap, maxWidth: Int): PreparedBitmap {
        val safeWidth = maxWidth.coerceAtLeast(1)
        if (bitmap.width <= safeWidth) {
            return PreparedBitmap(bitmap, bitmap.width, bitmap.height)
        }

        val ratio = safeWidth.toFloat() / bitmap.width.toFloat()
        val targetHeight = (bitmap.height * ratio).roundToInt().coerceAtLeast(1)
        val scaled = Bitmap.createScaledBitmap(bitmap, safeWidth, targetHeight, true)
        return PreparedBitmap(scaled, scaled.width, scaled.height)
    }

    private fun Bitmap.toJpegBytes(jpegQuality: Int): ByteArray {
        val out = ByteArrayOutputStream()
        if (!compress(Bitmap.CompressFormat.JPEG, jpegQuality.coerceIn(40, 95), out)) {
            throw IOException("Bitmap compression failed")
        }
        return out.toByteArray()
    }

    private fun buildPayload(
        config: Config,
        width: Int,
        height: Int,
        contentBase64: String,
    ): String {
        val fields: MutableMap<String, Any?> = linkedMapOf(
            "screen" to config.screenName,
            "deviceManufacturer" to Build.MANUFACTURER,
            "deviceModel" to Build.MODEL,
            "androidVersion" to Build.VERSION.RELEASE,
            "sdkInt" to Build.VERSION.SDK_INT,
        )
        fields.putAll(config.extraFields)

        val attachment = JSONObject()
            .put("kind", "image")
            .put("name", buildAttachmentName(config.screenName))
            .put("mime", "image/jpeg")
            .put("width", width)
            .put("height", height)
            .put("contentBase64", contentBase64)

        val root = JSONObject()
            .put("level", "info")
            .put("app", config.appName)
            .put("message", config.message)
            .put("fields", fields.toJson())
            .put("attachments", JSONArray().put(attachment))

        val traceId = config.traceId?.trim().orEmpty()
        if (traceId.isNotEmpty()) {
            root.put("traceId", traceId)
        }
        return root.toString()
    }

    private fun buildAttachmentName(screenName: String?): String {
        val prefix = screenName?.takeIf { it.isNotBlank() } ?: "screen"
        return "${prefix}_${System.currentTimeMillis()}.jpg"
    }

    private fun postLogs(config: Config, payload: String): SendResult {
        val baseUrl = config.serverUrl.trim().trimEnd('/')
        require(baseUrl.isNotEmpty()) { "serverUrl is empty" }
        require(config.appName.isNotBlank()) { "appName is empty" }

        val body = payload.toByteArray(Charsets.UTF_8)
        val connection = (URI("$baseUrl/logs").toURL().openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = config.connectTimeoutMs
            readTimeout = config.readTimeoutMs
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json")
        }

        try {
            connection.outputStream.use { it.write(body) }
            val status = connection.responseCode
            val responseBody = readResponseBody(connection, status)
            if (status !in 200..299) {
                throw IOException("Logdog HTTP $status: $responseBody")
            }

            val json = JSONObject(responseBody)
            val logId = if (json.has("id")) json.optLong("id") else null
            val ts = if (json.has("ts")) json.optLong("ts") else null
            return SendResult(logId = logId, ts = ts, responseBody = responseBody)
        } finally {
            connection.disconnect()
        }
    }

    private fun readResponseBody(connection: HttpURLConnection, status: Int): String {
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        return stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
    }

    private fun Map<String, Any?>.toJson(): JSONObject {
        val json = JSONObject()
        for ((key, value) in this) {
            if (value != null) {
                json.put(key, JSONObject.wrap(value))
            }
        }
        return json
    }

    private fun <T> runOnMainThreadBlocking(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return block()
        }

        val latch = CountDownLatch(1)
        var value: T? = null
        var error: Throwable? = null
        Handler(Looper.getMainLooper()).post {
            try {
                value = block()
            } catch (t: Throwable) {
                error = t
            } finally {
                latch.countDown()
            }
        }
        if (!latch.await(5, TimeUnit.SECONDS)) {
            throw IOException("Timed out waiting for the main thread")
        }
        error?.let { throw it }
        @Suppress("UNCHECKED_CAST")
        return value as T
    }

    private fun dispatchResult(
        onComplete: (Result<SendResult>) -> Unit,
        result: Result<SendResult>,
    ) {
        Handler(Looper.getMainLooper()).post {
            onComplete(result)
        }
    }
}
