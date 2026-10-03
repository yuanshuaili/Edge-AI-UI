# 部署稳定性、单份 UI 源码与多模态上传设计

日期：2026-10-03。状态：待用户审阅；不是已实现功能。

## 目标及实施边界

让现有 Nano 文本/语音展示更易操作，并让其他设备的模型开发者通过
profile + Adapter 接入图像/视频，不改前端、不在 UI 进程加载模型。
三个独立工作包顺序执行，每包先测试、再继续；不并行修改共享接口。

约定 DEPLOY_ROOT 为现有部署根，FRAMEWORK_ROOT 为已有独立 Git 工作目录。
本机启动参数/环境提供路径，不把本机用户名、地址或绝对路径写入共享核心。
正式 assistant.json 字节内容必须不变，以实施前后 SHA256 验证。
不移动虚拟环境、权重、量化缓存、llm-awq 或 flash-attention，不安装依赖，
不升级 CUDA/PyTorch/AWQ/sherpa-onnx，不重新量化，不启动模型/真实麦克风测试。
当前 Qwen 仍用原 3B W4A16/g128；single-active-LLM 和外部进程所有权规则保留。
不做 TTS、token streaming、多用户、浏览器麦克风、真实多模态模型加载。

## 工作包一：启动窗口及语音可发现性

### 已有证据与结论

用户日志中首次模型启动在 60 秒边界被 Manager 主动 TERM，随后报告
startup_timeout；不能推断是模型崩溃，也不能确认冷启动慢的具体原因。
重试模型已 ready；现场检查 ASR disconnected，未发现 ASR UI mode 进程。
当前麦克风控件只发采集控制，不启动 Python 服务；采集设备是边缘设备的麦克风。

### 变更

仅部署 Nano profile 的 startup_timeout_seconds 由 60 调为 180；Framework
通用默认值保持原行为。180 秒是保守可配置窗口，不是保证启动耗时的结论。
仍有明确 deadline，失败清理旧进程组及 endpoint，不自动重试/双份加载。
Manager 开发日志记录 attempt ID、开始、elapsed、ready/exit/timeout、清理结果。
llm_server 加时间戳阶段日志：导入完成、配置/tokenizer、权重、设备预热、
模型预热、socket ready；只加观测，不改推理/量化/加载顺序。
Launcher 用无缓冲 Python 输出，确保阶段日志及时写入每次 attempt 的日志。
导入尚未完成时明确只有 elapsed 可观测，不声称已知道每个第三方导入耗时。

麦克风加可见“语音输入”标签；闲置断连时显示“语音服务未连接”，busy 时
提示等待，不仅依赖灰色图标/title。保留 connected/capabilities/available_inputs/
ready/busy 门控与 listening 点击取消规则。初始化 HTML 不再写“即将接入”。
开发文档给出 ASR UI mode 独立启动命令和 socket 对齐方式；嘉宾 UI 不显示
Python 路径/端口/框架细节。本包不新增 ASR Lifecycle Manager；仍手动启动一份
ASR。可见语音输入按钮不是启动服务按钮，不以假 connected 掩盖未启动服务。

### 测试及范围

CPU-only 延迟 ready、超时清理、日志字段、旧端口释放、busy 门控回归；
浏览器 ASR 未连接提示、连接后启用、断开恢复、取消和中性超时提示。
新日志只能给下次实机测量提供证据；不得宣称已解决所有冷启动慢问题。

## 工作包二：部署整理与 Framework 唯一来源

### 方案选择

采用已有 FRAMEWORK_ROOT 为唯一 UI 源码与 Git 来源，部署通过 edge-ai-ui
目录链接访问。相比复制同步，避免两套源码漂移；相比整体移动 deployment，
保留 AWQ editable 安装、venv shebang、现有声卡/模型路径的兼容性。

目标部署布局：

```text
DEPLOY_ROOT/
├── config/                 # 现有正式配置
├── edge-ai-ui/             # 链接至 FRAMEWORK_ROOT
├── asr/                    # ASR/VAD 脚本，模型仍用现有路径
├── llm-awq/                # 原位置、不移动
├── models/                 # 原位置、不移动
├── awq_env/                # 原位置、不移动
├── asr_env/                # 原位置、不移动
├── tools/                  # 已核实路径的 benchmark/inspect 等脚本
├── archive/                # 带时间标识的备份和迁移清单
├── logs/
├── tests/                  # 部署专用兼容测试
├── docs/
├── ui_server.py            # 原命令兼容入口
└── asr_tinychat.py          # 原命令/导入兼容入口
```

SenseVoice 目录和 Silero 文件暂留原路径，不能因目录不美观擅自搬权重。
ui、ui_backend、integrations 的旧副本校验差异后归档；必要旧 import 名使用
指向唯一源码的兼容链接。根 ui_server.py 显式选部署配置和日志目录；Framework
入口保留独立使用方式。新增显式 assistant/log/config 参数，不能因 __file__.resolve
指向共享目录而读错中性示例身份。Nano launcher 明确传 EDGE_AI_TINYCHAT_ROOT；
llm_server 原 deployment product config 定位保持正确，不改 QwenPrompter 身份逻辑。
ASR 脚本进入 asr 后，根兼容入口保留 CLI 与测试 import 的公开函数/常量；
CLI/UI mode 共用 ASR/VAD 核心，UI mode 仍不直接 ask_tinychat。

迁移前记录每个文件目标、哈希、引用、Git 状态，检查已有用户改动，不覆盖。
检查脚本的 cwd/import/相对路径后才移入 tools；不能保证无影响的脚本先保留。
framework/ 中旧导出模板与唯一 repo 的差异也逐项核对后归档，不静默丢弃。
所有历史 .before_*、patch、截图、实验日志先归档，不认定“100% 无用”。
本阶段不递归删除任何文件，包括缓存；目录清理以可恢复迁移为准。
archive 只在部署本地，不加入共享仓库；.codex/.agents/.aws/.git 不搬动、不打包。

目录迁移需静止的部署环境：若 UI/Qwen/ASR 仍运行，停止在此包迁移边界并请
用户在原终端关闭。不能擅自杀当前展示进程或在线替换正在使用的源码路径。
不能用测试通过推断权重实机加载已验证。迁移失败按 manifest 恢复原路径，
不 reset 用户 Git 变更；对回滚中的链接和目标先检查精确路径。

### 验收

独立 Framework Mock 可启动，旧部署 UI 命令读正式产品配置；旧 ASR CLI 和
--ui-mode 导入/路由保持兼容；TinyChat product helper 导入仍可用；所有前端和
backend 模块实际来源唯一。部署+Framework 无模型回归均通过，正式 profile
哈希一致。共享 Git 排除真实配置、权重、venv、日志、archive 和上传文件。

## 工作包三：通用图像/视频附件和 Adapter

### 协议与功能边界

旧 text_chat(text)、image(payload)、video(payload) 接口不破坏：新增类型化
MediaRequest(text, attachment)，其中 Attachment 为不可变的 server-side 描述：
id、kind、已验证 media_type、size_bytes、受控 local_path。image/video 接收
MediaRequest 返回完整文本；默认 UnsupportedCapability。每轮最多一个附件，
允许空提问配附件；一张图或一段视频分别调用 image/video，不自动降级为文字。
Adapter 可以读取本地受控文件并上传远端服务；路径不会直接传给 Browser 或要求
另一台设备挂载同一个目录。无附件请求继续使用原 text_chat；ASR transcript 也一样。

统一 dispatcher 的 media/chat/reset 与生命周期共用 gate 和每 backend 锁。
只在当前 backend ready、空闲且 capability 与 available_input 都接通时接受。
ModelManager switching/starting/stopping 时禁止上传及推理；媒体推理时禁止
clear/switch。既有文件上传不加载模型，不改变 single-active-LLM 规则。

新增 HTTP：

- POST /api/attachments?kind=image|video&backend_id=...：一个 raw binary body，
  固定 Content-Length；响应只有 attachment_id、kind、size、media_type、expires。
- DELETE /api/attachments/<opaque_id>：删除未占用的临时附件，不接受 filesystem path。
- POST /api/chat：保留旧 {backend_id,text}，允许额外 attachment_id；不接受
  arbitrary path、URL、command、module、base64 大体积正文。

附件记录绑定提交时的 backend_id；消费时必须仍是当前所选 backend，服务端
检查能力、ready、TTL、占用/重复消费。切换或新对话清除前端待发附件，不自动
把上传给 A 的附件转给 B。占用附件 lease 确保推理期间 TTL/删除不移除文件。
处理成功/失败都释放本次 lease 并删除本次文件；失败前端保留用户消息、报错，
需重新选择附件再重试。不记录媒体内容到开发日志，不把媒体混入 profile。

### 有界上传与安全

仅用标准库处理文件流；每次 64 KiB、同一时间最多一个上传，正文超时 30 秒，
请求名额有界。默认每图 10 MiB、每视频 50 MiB、总附件 quota 100 MiB、最多
16 个临时附件、空闲 TTL 15 分钟；启动参数可配置且必须有限正值。
缺少/重复 Content-Length、Transfer-Encoding、长度超限、无效种类、错误 Content-Type、
无能力/未 ready 和 quota 满等情况在读正文前拒绝并关闭连接；中断/超时/短 body
清理 partial 文件。quota 原子预留，完成/失败释放，不能并发突破总限额。

允许 JPEG/PNG/WebP 与 MP4/WebM，检查有限文件头和 MIME 一致性；拒绝 SVG/HTML/
归档和未知类型。此检查仅用于格式筛选，不等同安全解码/完整媒体有效性验证。
UI 不安装解码器、执行上传内容、抽帧或解析完整视频；模型侧需有解码限制。
不执行文件名或 MIME。文件名随机生成，无客户端文件名作路径；专用私有临时目录，
目录/文件权限 0700/0600，拒绝 symlink/path traversal；API 只接受随机 opaque ID。
保持现有 same-origin/Host 检查，新增上传/删除遵循相同防护和 client slot 约束。
不提供 public 文件目录，不把原始媒体以 HTML 形式提供，也不把客户端 MIME 当可信。
有界后台清理 idle 附件，正常退出清理自己拥有的临时目录；不删除不属于本实例
的目录。异常退出的残留由 documented 本地维护流程处理，不遍历删除全局 /tmp。

浏览器预览使用用户选择文件的 object URL，在移除/切换/新对话/页面退出时 revoke；
不做 base64 复制、视频自动播放或波形效果。提示“本次模型不支持图像/视频”，
按钮状态真实反映接口能力。上传进度用浏览器原生进度事件，取消会中止并清理。
上传中的 backend selector 暂禁用；其他页面/API 切换导致上传取消或消费被拒绝。
服务端同时最多一轮媒体推理，取消 HTTP 不等于能中止模型，busy lease 保持至
Adapter 有界调用结束；前端不假装已停止模型，也不开放第二轮并发生成。

### 示例、共享与测试

新增独立 mock-media Adapter/profile，image/video 响应带 backend ID、种类、大小
及用户文字；明确写“模拟，未分析媒体内容”。不改现有 mock-demo 的基本能力，
不把 TinyChat/Qwen 文本 profile 的 image/video 设 true。
示例展示远端媒体 Adapter 的转换边界，README/add-a-model 增加接入、限制和安全
说明。真实多模态模型开发者实现 image/video + 注册 + profile 即可复用前端。
不承诺任意模型不写 Adapter 就兼容，视频抽帧/时长/分辨率限制由推理服务负责。

测试上传合法/超限/短 body/慢 body/并发/quota/TTL/取消/路径与 symlink/伪 MIME；
消费跨 backend、过期、重复、占用附件；Mock 图像/视频路由与 reset/switch gate；
浏览器选取、预览、移除、失败消息保留、能力切换、对象 URL 释放；原文本/ASR
回归。使用小型 fixture 文件与 CPU-only 模拟，不使用新权重或真实麦克风。

## 审阅与交付

用户先审阅本规格，再生成详细实现计划；执行方式沿用当前会话顺序实施、每包
独立测试，最后一次独立只读审阅和全量无模型回归，不让多个代理写共享接口。
最终报告新增/修改/迁移 manifest、恢复方式、单份源码证据、测试和 Git 状态；
保留原已确认 remote，是否发布新的代码 commit 按实施交付阶段确认，不创建远端。
真实冷启动耗时、RAM/swap、ASR 声卡和真实媒体模型效果均由用户最后实机验收。
