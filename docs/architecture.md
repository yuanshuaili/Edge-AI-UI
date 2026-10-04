# Architecture

```text
Browser ─ HTTP ─ UI backend ─ ChatDispatcher ─ selected BackendAdapter ─ model service
                    │              ↑
                    ├─ ModelManager (one worker, owned process group only)
                    │      └─ whitelist launcher → spawn / stop / verify health
                    └─ Unix ASR bridge ← independent ASR process
                           └─ transcript → ChatDispatcher → same selected adapter
                              statuses / answer → bounded SSE buffer → Browser
```

Identity belongs to Assistant Profile, not a particular model. Backend metadata
and capabilities come from configuration. UI presentation does not encode model,
device or transport names. Communication and process ownership are separate.

The dispatch gate atomically rejects chat/reset during lifecycle transitions;
the manager rejects transitions while any UI dispatch is active. ASR remains
independent, capture is canceled on switching and late request IDs are discarded.
One worker per model manager and one voice answer worker; HTTP clients and SSE
history are bounded. No additional model copies are imported into the UI.

External processes retain ownership outside this UI. Managed processes are spawned
in a separate session/process group. Normal UI exit closes only owned models;
SIGKILL of the UI itself, power loss and arbitrary daemonizing launchers are not
graceful shutdown and require operator inspection. Launchers must keep the model
and children in their original process group, never daemonize or detach sessions.
## 媒体路径

Browser File → bounded raw upload → private AttachmentStore → opaque ID
→ selected BackendAdapter.image/video(MediaRequest) → text reply。
媒体与文字、clear_session、ModelManager 共用 OperationGate 与 backend 锁；
附件 lease 防止在途删除，epoch/revision stamp 防止切换后的误投。
ASR 不参与媒体解码，UI 不加载推理依赖。
