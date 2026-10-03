# Edge AI Demo UI

A small, model-independent exhibition UI for Jetson and other Linux edge devices.
Plain HTML/CSS/JavaScript plus a Python standard-library HTTP backend. No npm
build, no GPU dependencies and no model import in the UI process. TinyChat is
optional: Mock works out of the box, and any text service can implement an adapter.

## Quick start (no model required)

Python 3.10+ on Linux; no pip packages required for UI or Mock.

```bash
cp config/assistant.example.json config/assistant.json
cp config/backends.example.json config/backends.json
python3 ui_server.py --host 127.0.0.1 --port 8080
```

Open http://127.0.0.1:8080 in a browser **on the same device**. For a remote device,
bind a trusted LAN interface and use that device's address; localhost is always
the browser's own machine. This is a trusted-local demonstration server, not an
authenticated public Internet service. Do not expose it on an untrusted network.

Select either Mock backend, send text, verify the response includes its backend
ID, and click 新对话. Voice stays disabled until a separate ASR client connects.
No model starts automatically in the sample profiles.

## Product identity (Assistant Profile)

Edit **your local** config/assistant.json: id, name, subtitle, welcome,
system_prompt. Change name/welcome to rename the assistant, without editing
frontend code. Only four public presentation fields go to the browser;
system_prompt stays server-side. Restart the UI to reload its presentation.
The adapter/inference service must apply this system prompt itself: the UI does
not prepend private instructions to user messages. Restart a model service if
you need its startup-loaded identity changed. A new conversation retains identity.

Real site config files are ignored by Git. Never replace another developer's
confirmed local Assistant Profile with the neutral example.

## Add a model

See [docs/add-a-model.md](docs/add-a-model.md): profile → BackendAdapter → explicit
server registry → optional launcher. No index.html/app.js/style.css edits.

- Adapter owns metadata, health and complete text_chat; optional clear_session.
- ModelManager owns process lifecycle, not model communication.
- External profile: connect only, never launch or kill it.
- Managed profile: whitelist launcher, finite start/stop timeouts, optional
  auto_start on the default backend. Commands/modules cannot come from JSON or HTTP.
- Capabilities describe potential support; available_inputs describe wired UI
  handlers. Voice also requires connected ASR, ready model and no busy operation.
- Image/video/streaming/TTS remain unsupported. Do not set available_inputs true
  for image/video before adding real handlers and validation in a future phase.

## Conversation and voice

新对话 → selected adapter.clear_session → success → remove frontend history and
restore welcome. Reset failure preserves the existing page record. The framework
supports one shared demonstration session, not separate user sessions.

Independent ASR → Unix JSON-lines events → UI coordinator → exactly the same
dispatch_text_chat as text input → selected adapter → SSE → browser. UI never
loads ASR models and browsers never connect to ASR directly. See
[voice integration](docs/voice-integration.md). ASR/model weights are not included.

## Process lifecycle

See [model lifecycle](docs/model-lifecycle.md). Only one managed large LLM may run
under this manager. On 8GB unified memory, stop and verify the old process group
and endpoint are gone **before** loading another. No preload/hot swap/parallel
large LLM. Other manually started programs cannot be safely controlled by this
manager: stop those yourself before starting another managed model.

The optional Nano launcher uses only W4A16/group128. Set EDGE_AI_TINYCHAT_ROOT in
the UI's environment to an existing deployment installation. There are no models,
venvs or TinyChat source in this repo; the generic UI does not require them.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Core/process tests use Mock and tiny CPU-only fixture processes, not LLMs. Browser
tests additionally use installed Chromium (skipped when absent). Test environments
must permit localhost TCP/Unix sockets. No real microphone is used. Deliberate
offline/timeout/KILL tests can produce expected developer warning logs.

Architecture: [docs/architecture.md](docs/architecture.md). This is a single-user
local demo baseline; authentication, multiuser sessions and streaming are not
implemented. Model-vendor licenses/weights remain the integrator's responsibility.
