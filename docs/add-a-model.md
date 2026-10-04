# Add a model without frontend changes

1. Add one entry to your ignored config/backends.json. Start by duplicating a Mock
   example; change id/name/device/mode/adapter/endpoint. IDs are safe identifiers.
   All text/voice/image/video/tts capability keys and input keys are required.
2. Implement BackendAdapter in a server-side Python module. Required: health()
   returning HealthStatus('ready'/'offline') and text_chat(text) returning a
   complete string. id/metadata/capabilities/available_inputs come from the base.
   See examples/custom_adapter.py. Use bounded timeouts, UTF-8 and safe errors.
3. Explicitly add your adapter class to ADAPTER_FACTORIES in
   ui_backend/adapter_registry.py, e.g. import ExampleAdapter then
   `"example": ExampleAdapter`. Set profile `"adapter": "example"`.
   Configuration is not a plugin loader: arbitrary Python module paths are rejected.
4. External server (including another Jetson/Thor): set runtime.managed false.
   UI only connects. Your adapter uses the configured service endpoint; do not
   load your model in the UI process. For custom transport validation, validate
   endpoint/schema in your adapter constructor.
5. Optional managed server: implement a trusted launcher returning LaunchSpec
   (tuple argv, cwd Path). Register it in MODEL_LAUNCHERS in
   ui_backend/model_launchers.py. See examples/custom_launcher.py. Configure:

```json
"runtime": {
  "managed": true,
  "launcher": "your_registered_launcher_id",
  "startup_timeout_seconds": 60,
  "shutdown_timeout_seconds": 10,
  "auto_start": false
}
```

The launcher owns flags/environment/path decisions. Never execute commands or
module paths from config/request data, never use shell=True. Browser API accepts
backend_id only. Keep all service children in the launcher's original process
group so shutdown can prove memory-bearing processes are gone. Health must not
report ready before the model is actually usable. A managed adapter also implements
endpoint_occupancy(): occupied/absent/unknown. Offline readiness is not proof of
port release; timeout/unreachable/permission failures must return unknown, not
absent. Unknown blocks launch/switch safely. At most the default backend
can have auto_start true; use one managed large model at a time.

For a stateful model implement clear_session(): request a real service reset,
verify acknowledgement and return {"ok": True}. Default UnsupportedCapability
is safe. Never claim success while history remains. Inference service should
apply config/assistant.json system_prompt on startup and preserve it on reset.
Optional stream_chat/image/video/voice default to unsupported; no need to implement
them for text + recognized transcript input. `voice` capability does not require
adapter.voice(): transcripts deliberately use text_chat().

Restart UI after code/config changes. Profile automatically appears in selector,
metadata, status and input capability gates. Verify Mock → your backend → Mock,
offline behavior and reset success/failure. No HTML/JS/CSS changes are needed to
add text services on Thor or other devices. Image/video handlers are now available
through typed optional adapter methods; capability declarations alone are not enough.
## 图像 / 视频

实现 image(MediaRequest) / video(MediaRequest)，在 registry 显式注册，
并设置 capabilities 和 available_inputs：前端无需修改。
参考 [媒体接入](media-integration.md) 与 examples/custom_media_adapter.py。
必须实现真正的接口才开放能力；上传本身不会给文本模型增加视觉理解。
