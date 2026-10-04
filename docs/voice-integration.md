# Independent ASR integration

Browser → /api/voice/control {"action":"start"/"stop"} → UI Unix socket →
one resident ASR process → transcript → current selected BackendAdapter.text_chat
via dispatch_text_chat → SSE → browser. ASR must not call the LLM in UI mode.
Keep any pre-existing ASR CLI→LLM path as a separate optional output mode.

Initialize recognizer/VAD once. For one start request, open capture, collect one
valid utterance, stop InputStream, decode and send transcript; do not unload models.
Use the same existing microphone selection/resampling/audio queue/VAD logic.
At max_listen_seconds (default20), close capture and send error code no_speech.
Cancellation invalidates the request; ignore transcripts from canceled/old IDs.
Model transition cancels capture and disables mic; ASR process is never stopped
by ModelManager. No TTS or continuous multi-utterance capture in this phase.

Unix socket is local mode0600, one ASR client. Default path derived from OS uid;
override with UI --asr-socket and match the ASR connection path. Browser never
knows this path. JSON-lines UTF-8, version1, size≤16KiB, transcript≤4000 chars:

```json
{"v":1,"type":"start_listening","request_id":"r1","max_listen_seconds":20}
{"v":1,"type":"stop_listening","request_id":"r1"}
{"v":1,"type":"status"}
{"v":1,"type":"listening","request_id":"r1"}
{"v":1,"type":"speech_detected","request_id":"r1"}
{"v":1,"type":"recognizing","request_id":"r1"}
{"v":1,"type":"transcript","request_id":"r1","text":"你好"}
{"v":1,"type":"error","request_id":"r1","code":"no_speech","message":"no speech"}
```

Use ui_backend.asr_client.AsrUiClient and voice_protocol encode/decode helpers in
your ASR process. Events are validated; private ASR errors become safe UI hints.
An idle event can acknowledge cancellation. See implementation for status framing.

SSE endpoint /api/voice/events uses `event: voice`, sequential id, JSON data with
type and epoch. Types: state, selection, asr_status, backend_busy, model_state,
listening, speech_detected, recognizing, transcript_ready, thinking, answered,
idle, error. transcript_ready/thinking/answered carry request_id and backend_id;
answered includes text/latency_ms. Thinking belongs to UI, never to ASR. The last
128 events are retained, Last-Event-ID replay supported, invalid/old cursors get
a fresh state snapshot. Epoch distinguishes server restarts. This is single-user
coordination, not multiuser or token streaming.
## 可见语音入口

麦克风旁显示“语音输入”，下方显示未连接/模型准备中/等待交互/可点击。
按钮控制边缘设备上的麦克风，不启动 ASR 服务，也不使用浏览器麦克风。
启动顺序：UI → 页面启动模型并等待 ready → 独立 ASR UI mode。
部署例：`asr_env/bin/python asr/asr_tinychat.py --ui-mode`。
旧的根 asr_tinychat.py 命令兼容。UI --asr-socket 与 ASR --ui-socket 必须一致；
默认都是按当前用户的本地 Unix socket。保持一份常驻识别器；一次点击识别一句，
再次点击取消；20秒无有效语音恢复 idle。真实声卡必须另外实机验收。
