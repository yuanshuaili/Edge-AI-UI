# Edge AI UI

面向 Jetson、Thor 和其他 Linux 边缘设备的轻量展示 UI。浏览器负责显示与输入，
Python 标准库服务负责适配、进程协调与有界上传；**UI 进程不加载模型**。
无需 npm 构建或大型 Web 框架，也不要求使用 TinyChat。

已实现文本问答、新对话、模型选择与生命周期管理、独立 ASR 语音输入、图片/视频
上传与预览。视觉理解由接入的模型提供，上传功能本身不等于多模态推理能力。
当前是单用户展示会话；不包含 TTS、token streaming、多用户隔离或公网认证。

## 1. 五分钟启动：不需要模型

Linux、Python 3.10+ 和浏览器即可。UI/Mock 不需要 pip 依赖。

~~~bash
git clone https://github.com/yuanshuaili/Edge-AI-UI.git
cd Edge-AI-UI
# 仅首次创建本机配置；已存在则不覆盖。
test -e config/assistant.json || cp config/assistant.example.json config/assistant.json
test -e config/backends.json || cp config/backends.example.json config/backends.json
python3 ui_server.py --host 127.0.0.1 --port 8080
~~~

在**同一设备**打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。选择“演示后端”，
发消息并确认回答含 `[mock-demo]`，点击“新对话”恢复欢迎页。选择“媒体演示后端”
可测试图片/视频，但它明确回复“模拟，未分析媒体内容”，不会加载模型。
独立 ASR 未连接时语音按钮禁用。

另一台电脑的 `localhost` 指那台电脑，不是边缘设备。远程浏览时将 `--host` 设为
设备的**可信局域网接口地址**，用该地址打开；额外主机名用 `--allowed-host`。
本服务没有用户认证，不能直接暴露公网或不可信展会 Wi-Fi。

配置可与源码分离：

~~~bash
python3 ui_server.py --host 127.0.0.1 --port 8080 \
  --config /path/to/deployment/config/backends.json \
  --assistant-config /path/to/deployment/config/assistant.json \
  --model-log-dir /path/to/deployment/logs
~~~

以上路径是占位符。真实配置、密钥、权重、venv、上传文件和日志不属于共享仓库。

## 2. 架构与职责

~~~text
Browser（通用 HTML / CSS / JS）
    ↓ HTTP / SSE
UI Backend
    ├─ BackendAdapter → 文本 / 多模态模型服务
    ├─ ModelManager   → 白名单 Launcher → 唯一受管大模型进程
    ├─ 私有附件       → MediaRequest → 当前 Adapter
    └─ Unix socket   ← 独立常驻 ASR → transcript → 同一 text_chat
~~~

Adapter 决定如何通信，Launcher 决定如何启动，Profile 描述产品与模型。
接新模型不需要修改 `ui/index.html`、`ui/app.js`、`ui/style.css`。
ASR 和模型服务可保留各自的 Python/CUDA/SDK 环境。

## 3. Assistant Profile：修改“小A”或产品名称

编辑本机 `config/assistant.json`，不要修改前端：

~~~json
{
  "id": "my-assistant",
  "name": "我的助手",
  "subtitle": "本地 AI 助手",
  "welcome": "你好，有什么可以帮你？",
  "system_prompt": "你是我的助手。使用自然、准确的语言回答用户。"
}
~~~

`id/name/subtitle/welcome` 公开给浏览器，`system_prompt` 不通过 UI API 返回。
重启 UI 刷新展示身份。**推理服务或自定义 Adapter 必须将 system_prompt 放入其
system message**：UI 不把私有指令拼进用户文本，不能自动约束任意第三方模型。
Nano 集成的推理服务在启动时读取 Profile，改后需重启模型。
reset 必须保留身份。模型遵循指令的可靠性需单独验收，不能只靠 UI 名称保证。
不要把已经确认的部署 Profile 替换为示例。

## 4. 接入新文本模型：完整步骤

### A. 准备独立服务

在自己的模型环境先启动服务，确认能接受文本并返回完整答案。可以在同一 Jetson
或另一台 Thor 上运行。**不要在 Adapter 中 import/load 模型**。
确认请求地址、鉴权、输入/输出协议、超时和会话重置方法。

### B. 实现 BackendAdapter

可复制 [examples/custom_adapter.py](examples/custom_adapter.py)。这是**可运行的
无模型接入示例**，不是某个厂商 API 的实现：

~~~python
from ui_backend.adapter_base import BackendAdapter, HealthStatus


class ExampleAdapter(BackendAdapter):
    def health(self) -> HealthStatus:
        return HealthStatus("ready")

    def text_chat(self, text: str) -> str:
        return f"示例后端[{self.id}]收到：{text}"

    def clear_session(self):
        return {"ok": True}  # 无状态示例；真实模型必须确认远端历史已清空。
~~~

真实接入时替换这三个方法：

1. 从 `self._profile.endpoint` 读取地址，在构造函数验证协议/主机/路径。
2. `health()` 用短、有界探测返回 `HealthStatus("ready")` 或 `"offline"`。
   仅当服务加载完成才监听时，“端口可连”才能代表 ready。
3. `text_chat(text)` 构造**实际服务接受**的 HTTP/JSON、TCP 或 SDK 请求，提取结果，
   返回一个完整 `str`，不是 generator/token stream。
4. transport 设置连接、读取、总 deadline 和回复大小上限。API 密钥来自服务器
   环境或受保护配置，不放进浏览器或公开 profile。
5. 错误映射为 `ui_backend.adapters` 中的 `BackendUnavailable`、`BackendTimeout`、
   `BackendProtocolError` 等。message 可显示给嘉宾，只能是安全文案；
   URL、token、traceback 和供应商错误原文只保留在开发日志。
6. 有状态服务的 `clear_session()` 真实清空历史并返回 `{"ok": True}`。
   没有 reset 能力就保留默认 `UnsupportedCapability`，不能伪造成功。

例如服务接受 `{"message":"你好"}`、返回 `{"answer":"你好！"}`，Adapter 应完成
`text → message` 与 `answer → str` 映射；其他 schema 要相应调整。
Framework 不猜测模型协议。[TinyChatAdapter](ui_backend/adapters.py) 提供真实的
有界 JSON-lines TCP 参考，但 HTTP 模型不必依赖它。

基类提供 `id/metadata/capabilities/available_inputs`，仅 `health/text_chat` 强制实现。
`stream_chat/image/video/voice/clear_session` 默认不支持；转写输入走 `text_chat`，
不要求实现 `voice()`。受管模型另需 `endpoint_occupancy()`，见第 6 节。

### C. 在服务器显式注册

在 `ui_backend/adapter_registry.py` 导入类，并保留已有注册项：

~~~python
from examples.custom_adapter import ExampleAdapter

# 放在已有 ADAPTER_FACTORIES 定义之后，保留其他条目。
ADAPTER_FACTORIES["example"] = ExampleAdapter
~~~

字典值接收 `BackendConfig`。配置不是插件加载器，不能填写任意模块/代码/command。

### D. 增加 backend profile

把此对象加到本机 `config/backends.json` 的 `backends` 数组，`id` 必须唯一。
顶层 `default_backend_id` 必须指向已有条目，可保留 Mock 或改为新 ID。

~~~json
{
  "id": "my-model",
  "name": "我的模型",
  "device": "Jetson Thor",
  "mode": "Local / External",
  "adapter": "example",
  "endpoint": "mock://local",
  "runtime": {"managed": false},
  "capabilities": {"text": true, "voice": true, "image": false, "video": false, "tts": false},
  "available_inputs": {"text": true, "voice": true, "image": false, "video": false}
}
~~~

`example + mock://local` 能直接测试接入流程。真实模型要替换 Adapter transport 和
endpoint；只改 endpoint 不会让 echo 示例变成模型客户端。
`name/device/mode` 显示给嘉宾，不应包含实现层日志或私有地址。
可选 `runtime_model_name` 供服务器集成使用，不代替展示名。

### E. 重启并测试

新后端自动出现在 selector。验证 Mock → 新模型 → Mock、离线状态、文本答案、
新对话成功/失败和生成期间按钮禁用。接通 ASR 后确认语音走同一个当前 Adapter。
更多细节见 [docs/add-a-model.md](docs/add-a-model.md)。

## 5. 能力与按钮：capabilities / available_inputs

两份映射均需包含示例中的全部键，布尔值不能省略。

| 输入 | capabilities | available_inputs | 额外条件 |
| --- | --- | --- | --- |
| text | 能回答文本 | 已接通 text_chat | 当前模型 ready、不忙 |
| voice | 能回答转写文本 | 独立 ASR 已接通 | ASR connected、ready、不忙、不切换 |
| image | 模型能理解图片 | 已实现 image() | ready、上传通道可用 |
| video | 模型能理解视频 | 已实现 video() | ready、上传通道可用 |
| tts | 保留能力字段 | 无该输入键 | 当前 UI 不实现 |

`available_inputs=true` 不得超过 `capabilities`，voice 还必须有 text 输入。
启用 image/video 却没有相应 Adapter 方法会拒绝配置。
纯文本模型不要打开视觉能力；Mock 不代表真实视觉理解。

## 6. 可选：让 UI 启停模型

**external**：`runtime.managed=false`，你独立启动模型，UI 只连接，不停止它。
**managed**：ModelManager 只运行代码白名单中的 Launcher，只关闭自己拥有的进程。
浏览器只提交 `backend_id`，不能提交 shell command。

参考 [examples/custom_launcher.py](examples/custom_launcher.py)：

~~~python
import os
from pathlib import Path
from ui_backend.model_launchers import LaunchSpec


def example_launcher(profile):
    root = Path(os.environ["EXAMPLE_MODEL_ROOT"]).resolve()
    return LaunchSpec(
        (str(root / "venv/bin/python"), "-m", "example_model.server"), root
    )
~~~

模板中的服务/参数需替换为实际固定启动参数。在
`ui_backend/model_launchers.py` 的 `LaunchSpec` 和已有 registry 定义后加入：

~~~python
from examples.custom_launcher import example_launcher

MODEL_LAUNCHERS["example_launcher"] = example_launcher
~~~

这样保留已有条目，且避免模板导入 `LaunchSpec` 时的循环初始化。
配置只引用白名单 ID：

~~~json
"runtime": {
  "managed": true,
  "launcher": "example_launcher",
  "startup_timeout_seconds": 180,
  "shutdown_timeout_seconds": 10,
  "auto_start": false
}
~~~

Adapter 必须实现 `endpoint_occupancy()`：`"occupied" / "absent" / "unknown"`。
只有确认 endpoint 释放才返回 absent；超时/权限错误是 unknown，不是释放。
unknown 阻止新模型加载。服务及其子进程不能 daemonize 或脱离原进程组。

状态为 `offline/starting/ready/stopping/switching/error`。切换顺序：
禁止请求 → 停旧受管进程组 → 等退出 → 确认 endpoint 释放 → 启新模型 →
health ready → 更新选择。不预加载、不 hot swap、不自动 rollback。

Nano 8GB 尤其必须 single-active-LLM。保证范围是管理器拥有的进程和已声明受管
endpoint，不包括任意第三方手动启动的模型。手动模型需要其原 owner 先停止。
正常 UI 退出会关闭 owned 模型，不误杀 external；强制 kill/掉电不能保证 cleanup。
ASR 始终独立，不归 ModelManager 管理。

`auto_start=false`：页面选择/点击“启动模型”后启动。
`auto_start=true`：仅默认 managed backend 能在 UI 启动时自动启动。
详情见 [docs/model-lifecycle.md](docs/model-lifecycle.md)。

可选 Nano 集成 `tinychat_qwen25` 用服务器环境 `EDGE_AI_TINYCHAT_ROOT` 定位既有
安装，固定 Qwen2.5-3B-Instruct W4A16/group128，不提供 FP16/W16A16 UI 选项。
权重、TinyChat、CUDA 和 venv 不在仓库中，generic UI 不需要它们。

## 7. 图片/视频多模态接入：完整步骤

### 调用链

~~~text
浏览器选一个 File → 本地预览 → 有界 raw binary 上传
→ attachment_id → /api/chat → selected Adapter.image/video(MediaRequest)
→ 自己的视觉模型服务 → 完整文本答案 → 浏览器
~~~

UI Backend 不解码媒体、不抽帧、不加载视觉权重。模型协议、抽帧和理解属于 Adapter/
模型服务；浏览器原生预览仍会解码，因此客户端也要评估异常分辨率/时长的资源开销。

### A. 先测已有 mock-media

选择媒体演示后端，上传 JPEG/PNG/WebP 或 MP4/WebM，预览后发送，文字可为空。
确认模拟响应、取消/移除、切纯文本后按钮禁用、新对话使待发附件失效。
一次只允许一个附件，不同时发送图和视频。浏览器不提交任意 URL 或本机 path。

### B. 实现 typed media 方法

以下方法放进自己的 BackendAdapter 子类：

~~~python
from ui_backend.media import MediaRequest


def image(self, payload: MediaRequest) -> str:
    # payload.text：用户文字，可为空。
    # attachment：kind / media_type / size_bytes / 私有 local_path。
    return self.send_to_visual_service(payload)


def video(self, payload: MediaRequest) -> str:
    return self.send_to_visual_service(payload)
~~~

`send_to_visual_service` 由接入者实现，不是基类方法。
完整边界模板在 [examples/custom_media_adapter.py](examples/custom_media_adapter.py)：
注入同步 `transport(kind, text, MIME, byte_chunks) -> str`，每块读取 64 KiB。
你必须实现实际 API schema、鉴权、health、时间和回复大小限制。
可通过子类绑定**可信服务器 transport**，然后注册实际类：

~~~python
from examples.custom_media_adapter import CustomMediaAdapter
from my_integration.transport import make_transport  # 自己实现，不由 JSON 动态导入。


class MyVisionAdapter(CustomMediaAdapter):
    def __init__(self, profile):
        super().__init__(profile, transport=make_transport(profile.endpoint))

    # 真实接入还应覆盖 health()；需新对话时实现 clear_session()。
~~~

不要直接注册仍需额外 `transport` 参数的模板。
transport 必须在返回前同步消费完文件；方法返回后 lease 释放，不能把路径交给
异步后台任务。远端 Thor/云端不能读取 UI 机器的 `local_path`，要传文件字节或
按自己的受保护上传/引用协议获取远端媒体 ID。
SDK 若要求 base64，接入者单独评估其复制/编码内存，UI 核心不转换整个视频。

### C. 注册并启用实际支持的能力

在 `ADAPTER_FACTORIES` 注册 `"my-vision": MyVisionAdapter`，添加第 4 节形式的
profile，adapter 改为 `my-vision`，endpoint 改为实际服务地址。
只支持图片时设置：

~~~json
"capabilities": {"text": true, "voice": true, "image": true, "video": false, "tts": false},
"available_inputs": {"text": true, "voice": true, "image": true, "video": false}
~~~

真实支持视频后才把两个 video 值设 true。重启 UI，selector/按钮自动更新，
不用改 HTML/JS/CSS。如果模型仅接受视频抽帧图片，帧数/间隔/分辨率等策略由服务
实现；Framework 不假装自动完成它。

### D. 限制资源并验收理解能力

默认图片 10 MiB、视频 50 MiB、总附件 100 MiB、16 个记录、闲置 TTL 15 分钟、
上传总 deadline 30 秒。支持 JPEG/PNG/WebP、MP4/WebM，拒绝 SVG/HTML。
`python3 ui_server.py --help` 可查看参数。每块 64 KiB、一个上传和一个媒体 lease。
私有目录 0700、文件 0600；不提供原始附件下载/托管。

文件头筛选**不是完整安全解码验证**。模型服务要验证实际格式，限制像素、帧数、
时长、抽帧率、解码资源、并发和总推理时间；不可信媒体应隔离解码。
用已知内容验证模型确实理解图片/视频，再测试超限、格式错误、离线、超时、
取消、换模型和 reset，并检查 RAM/GPU 峰值。Nano 不同时加载第二个大模型。

接口返回 opaque ID，不返回本机路径：

~~~text
POST   /api/attachments?kind=image|video&backend_id=ID  （raw body + 唯一 Content-Length）
POST   /api/chat {"backend_id":"ID","text":"描述它","attachment_id":"opaque ID"}
DELETE /api/attachments/ID                           （移除未占用附件）
~~~

HTTP 取消/前端超时不等于终止模型。busy 保留到实际 Adapter 调用完成，不能立刻
开始新推理/启动新模型。发送失败保留用户消息；媒体重试需重新选择附件。
协议细节见 [docs/media-integration.md](docs/media-integration.md)。

## 8. 语音和新对话

语音控制的是**边缘设备上的麦克风**，不是浏览器麦克风。独立 ASR/VAD 只加载一次：
点击采集一句 → 有效语句后停 InputStream → 识别 → transcript → 当前 `text_chat`。
再次点击取消，默认 20 秒无有效语音回 idle；丢弃已取消 ID 的迟到 transcript。

启动顺序：UI → 模型 ready → 独立 ASR UI mode。
Framework 不包含识别权重或完整 ASR 程序。接入者用
`ui_backend.asr_client.AsrUiClient` 在既有 ASR 中对接 Unix JSON-lines，
UI mode **只输出 transcript，不直接调用 LLM**，原 CLI 路径可独立保留。
协议、事件和 Nano 启动示例见 [docs/voice-integration.md](docs/voice-integration.md)。

文本/语音最终共用 selected Adapter.text_chat。ASR 断开、模型未 ready/忙碌/切换
时禁用 mic；不会在 selector 切换后继续把语音投到旧模型。

“新对话”：selected Adapter.clear_session → 确认成功 → 清页面并恢复欢迎。
失败保留记录，生成/切换时拒绝 reset。不重载权重/ASR，不清 Assistant Profile。
这是一个共享展示会话，不是每个浏览器独立会话。

## 9. 安全与展会边界

- 默认 loopback，Host/Origin 限制不是认证。可信 LAN 客户端仍可操作服务；
  不直接暴露公网或不可信网络。
- API 校验浏览器 Fetch Metadata，SSE 使用同源页面 nonce 和独立的 4 连接配额；
  支持重启后的自动重连，并禁止其他网站 iframe 嵌入。它们不代替用户认证。
- 模型答案、用户文字和文件名以文本显示，不执行 HTML；浏览器不能提供 command、
  模块路径、附件 path/URL。Adapter/Launcher 是可信服务器代码，不是沙箱插件。
- transport/密钥、第三方 SDK、media 解码器和模型输出策略由接入者审查。
- 展会前验证干净启动内存、长时间运行、启停/断线/取消和现场噪声，关闭额外开发
  浏览器/工具。Mock 测试不代替真实模型、麦克风或视觉质量验收。
- 正常退出清理 owned 模型和附件；强制退出可能残留，按确切实例核查，不递归删除
  整个 `/tmp` 或误杀外部模型。
  正常退出也等待后台语音推理，130 秒 drain 超时会保留仍在使用的资源并写开发日志。

详见 [docs/security.md](docs/security.md)。厂商模型许可证、权重和 SDK 由接入者负责；
不要将私有模型、现场录音、上传文件或密钥提交到共享仓库。

## 10. 测试与排障

干净克隆可直接跑，不依赖私有配置：

~~~bash
python3 -m unittest discover -s tests -v
python3 scripts/audit_repository.py
git diff --check
~~~

HTTP/Unix socket/CPU fixture 测试不启动真实 LLM/麦克风。浏览器测试需要已安装的
Chromium，没有时会 skip；环境需允许 localhost TCP/Unix socket。故意 offline/
timeout/SIGKILL 的用例可能产生预期日志。

- 页面打不开：检查 UI 进程；跨机器不要使用 localhost。
- 模型失败：检查开发日志、ready probe、加载 deadline、旧 endpoint 释放、环境/路径；
  不要并行反复启动模型。
- 新模型未出现：确认 `--config`、注册项和重启，查看配置错误；JSON 不导入 Python。
- 语音禁用：voice 两个字段、ASR connected、ready、不忙、Unix socket 一致。
- 视觉禁用：核对 capability/available_inputs/方法实现和 ready。
- 能上传但不能理解：检查真实视觉模型和 transport，不把 Mock 当成视觉推理。
- 错误后消息仍在：预期行为，不因 timeout/reset 失败删除嘉宾的问题。

进一步阅读：[架构](docs/architecture.md) · [新增模型](docs/add-a-model.md) ·
[生命周期](docs/model-lifecycle.md) · [多模态](docs/media-integration.md) ·
[语音](docs/voice-integration.md)。
