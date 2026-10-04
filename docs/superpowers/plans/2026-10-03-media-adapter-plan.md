# 通用图像视频附件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新模型只实现/注册 Adapter 与 profile 就能复用图片/视频选择、上传、预览和回答。

**Architecture:** 浏览器 raw binary 上传到有界磁盘 AttachmentStore，返回 opaque ID；chat 消费 lease，经过既有 operation gate/锁调用 image/video。UI 不解码媒体、不加载模型，Nano Qwen 的视觉 capability 保持 false。

**Tech Stack:** Python 标准库、unittest、原生 XMLHttpRequest/File/object URL，已有 Chromium；不安装 SDK/解码器/框架。

**Spec:** `docs/superpowers/specs/2026-10-03-deployment-unification-media-design.md` 工作包三。

## Global Constraints

继承索引 Global Constraints。F 中实现新功能，D 通过计划2的链接直接使用，不复制代码。
单轮最多1附件；64 KiB块；单上传；30秒总 body deadline；图10 MiB、视频50 MiB、quota100 MiB、最多16个 idle/占用/上传记录、TTL900秒。启动参数有限正值（含bool/NaN/Inf拒绝），阈值0不允许；配置大小/TTL不允许突破服务器文档硬上限：单文件512 MiB、总 quota1 GiB、计数128、TTL86400秒、上传deadline120秒。目录0700/文件0600。
格式仅JPEG/PNG/WebP/MP4/WebM，前缀筛选不是安全解码验证；SVG/HTML/未知格式拒绝。媒体不公开托管，不传base64/path/任意URL到chat；不接入真实视觉模型。

## Review Focus

1. A→B→A 时上传结束回到同 ID：selection stamp而不只是ID必须一致（Task1/2）。
2. lease/TTL/退出竞态：busy文件不能删除，正常完成后可回收（Task1/2）。
3. 慢传每块及时但总耗时超过30秒：使用 monotonic总 deadline，不仅 socket idle timeout（Task2）。
4. body不足/重复长度/TE混合触发请求走私：早拒绝/关闭连接/清理partial（Task2）。
5. AbortError/旧页面/SSE restart 丢用户消息或媒体投到新选项：关联请求与backend，不提前释放服务器busy（Task3）。

### Task 1: 类型化请求与有界存储

**Files:** 新增 F:ui_backend/media.py、attachments.py；新增 tests/test_attachments.py；修改 .gitignore 排除 uploads/、附件状态及partial。

**Interfaces:** frozen `SelectionStamp(backend_id: str, epoch: str, revision: int)`、`Attachment(id: str, kind: Literal["image","video"], media_type: str, size_bytes: int, local_path: Path)`、`MediaRequest(text: str, attachment: Attachment)` 定义在 media.py。`AttachmentLimits(image_bytes=10485760, video_bytes=52428800, quota_bytes=104857600, max_attachments=16, ttl_seconds=900, upload_seconds=30, chunk_bytes=65536)` frozen dataclass，chunk_bytes固定不可调，其余含上述有限正值/硬上限验证。
`AttachmentStore(limits: AttachmentLimits, parent_dir: Path | None = None, clock=time.monotonic)` 创建 mkdtemp 私有目录；`reserve(stamp: SelectionStamp, kind: str, size: int, content_type: str) -> UploadReservation` 原子预留；`receive(reservation, read_chunk: Callable[[int,float],bytes], deadline: float) -> Attachment`；`consume(id: str, stamp: SelectionStamp)` contextmanager产Attachment；`discard(id: str) -> bool`；`invalidate_idle() -> None` 取消partial且删除idle、不删lease；`expire() -> int`；`close() -> None`。`AttachmentError(code: str, message: str, http_status: int)` 仅安全文案。

- [ ] **1：写失败测试。** 默认值与硬上限精确断言；legal image/video small fixture、MIME不符/SVG、size不符、quota预留及超额、第二上传拒绝、16计数限、clock推进900过期；opaque ID非法/跨stamp/消费两次拒绝；symlink替换拒绝；consume期间discard/TTL/close不删文件、释放后删且quota归零。

  ```python
  limits = AttachmentLimits()
  self.assertEqual((limits.image_bytes, limits.video_bytes, limits.quota_bytes), (10 << 20, 50 << 20, 100 << 20))
  self.assertEqual((limits.max_attachments, limits.ttl_seconds, limits.upload_seconds, limits.chunk_bytes), (16, 900, 30, 65536))
  with store.consume(attachment.id, stamp) as leased:
      self.assertTrue(leased.local_path.is_file())
  self.assertFalse(leased.local_path.exists())
  ```
- [ ] **2：验证 RED。** F：`python3 -m unittest discover -s tests -p test_attachments.py -v`；缺少module为预期RED。
- [ ] **3：实现。** 锁保护reservation/record/lease/closing、随机名字和独占创建；chunk不大于65536，失败finally删除本实例partial并释放quota。有限读取头识别PNG签名/JPEG起始/WebP RIFF+WEBP/MP4有限ftyp品牌/WebM EBML前缀；保持type与种类匹配，不伪称完整解码安全。每记录保存SelectionStamp，仅HTTP metadata不暴露local_path。一个有界maintenance线程周期调用expire，close标closing/取消上传/清idle，leased延迟至release回收；最后一个本实例记录结束才删除自己目录，不清理外部parent。
- [ ] **4：验证 GREEN。** 重跑目标测试；检查new store close可重复，禁止越界删除，公开metadata不含路径/文件名；未正常退出留下目录的维护说明进入Task4。
- [ ] **5：提交。** `git add ui_backend/media.py ui_backend/attachments.py tests/test_attachments.py .gitignore`；`git commit -m "feat: add bounded private attachment storage"`。

### Task 2: 媒体 dispatch、HTTP 安全和 Mock

**Files:** 修改 F:ui_backend/chat.py、server.py、config.py、adapter_base.py、adapter_registry.py、ui_server.py；新增 ui_backend/attachment_http.py、media_adapters.py、tests/test_media_dispatch.py、test_attachment_http.py；扩展tests/test_adapter_contract.py、test_lifecycle_http.py。config/backends.example.json新增mock-media；D:config/backends.json只新增同profile、不改assistant/旧capabilities。

**Interfaces:** `BackendAdapter.image(payload: MediaRequest) -> str` / `video(...) -> str` 仍默认UnsupportedCapability；旧参数调用兼容，text_chat不变。`ChatDispatcher.dispatch_media_chat(backend_id: str, text: str, attachment: Attachment, stamp: SelectionStamp) -> ChatResult`；`set_selection_provider(provider: Callable[[],SelectionStamp]) -> None`。Provider由server读取manager.snapshot（backend/epoch/revision）；进operation gate后再次核对。
`AttachmentHttp(store, dispatcher, selection_provider)` 提供 `upload(handler, query: dict) -> None`、`delete(handler, id: str) -> None`，共用现有host/error/json逻辑；handler供带deadline的bounded reader。`create_server(..., attachment_limits=None, attachment_parent_dir=None)`；main新增 `--max-image-mib/--max-video-mib/--attachment-quota-mib/--max-attachments/--attachment-ttl-seconds/--upload-timeout-seconds/--attachment-parent-dir`，parent只能本机CLI提供。上传response的expires为UNIX timestamp，store内部TTL始终用monotonic；公开limit字段为image_bytes/video_bytes/quota_bytes/max_attachments/ttl_seconds/upload_seconds及allowed_mime_types。

- [ ] **1：写失败测试。** 旧{text}响应完全相同/ASR不进media；image/video调用正确，UnsupportedCapability不降级；slow Adapter期间gatebusy，reset/switch/voice拒绝；异常后busyfalse且lease删除。HTTP分别验证 raw body/空text+附件、未知/多余字段/path/URL/session拒绝、未ready/transition/capability/input缺一拒绝、A→B→A stamp失效、double consume、no origin/expected origin允许而跨origin拒绝。

  ```python
  result = dispatcher.dispatch_media_chat("mock-media", "", attachment, stamp)
  self.assertIn("mock-media", result.text)
  self.assertIn("模拟，未分析媒体内容", result.text)
  self.assertEqual(adapter.text_calls, 0)
  self.assertEqual(response_status_for_expired_stamp, 409)
  self.assertEqual(response_status_for_cross_origin, 403)
  ```
- [ ] **2：添加资源/协议失败测试。** raw TCP验证缺少/重复/负CL、CL+TE、TE单独、oversize在发送body前回应且close；慢滴灌超过总deadline、断连短body、额外querykind/id重复拒绝；quota满/第二上传/客户端取消partial清理；lease期间UI normalclose等待bounded操作、异常shutdown残留不误删leased/外部目录。
- [ ] **3：验证 RED。** F：`python3 -m unittest discover -s tests -p test_media_dispatch.py -v` 和 `-p test_attachment_http.py -v`；localhost socket使用授权运行，时间测试clock注入不真的等30秒。
- [ ] **4：实现 dispatch。** 提取chat现有锁/gate/busy通知的共用执行块，不改text行为；media有文本4000字限，调用selected image/video且校验返回str/完整reply长度，异常映射安全BackendError。stamp包含managerrevision防换走换回，ready/offline以短adapterhealth在gate内确认。占lease和busy直到Adapter返回；HTTP timeout不等于取消Adapter。一个store lease占用限制使UI最多一轮媒体生成，库测试不依赖Browser。
- [ ] **5：实现 HTTP。** attachment路由放JSON读body之前；删除只验证opaque ID；所有状态变更保留Host并校验非空Origin与当前可信UI origin一致，不能把Host当身份认证。binary Content-Type与种类允许表核对；原请求长度上限16384不放宽媒体JSON。读块的socket timeout取remaining deadline与idle期限较小者，用read1或等价不会读完大body才返回的方法。先预留、再读取、读完重检stamp，失败统一abort与close。Expect:100-continue必须通过相同预检查再发100，否则早拒绝，不先承诺读body。
- [ ] **6：接生命周期/清空/关闭。** manager开始transition或reset成功invalidate idle/partial；原listener继续发SSE/voice，不替换掉既有observer。HTTP关闭先禁止新附件/取消上传，等待有界active media/handlers返回再storeclose/managerclose；超过130秒开发警告并保留仍占用文件，属于异常退出维护，不强删；正常结束无临时文件。不存在无界后台线程/上传队列。
- [ ] **7：Mock与配置。** `MockMediaAdapter` 独立注册为mock-media，响应包含id/kind/size/text和“模拟，未分析媒体内容”；text与clear可用，voicefalse。在IMPLEMENTED_INPUTS加入image/video，但按接口实现存在/declared capability验证，不只config宣称即启用。Qwen/mock-demo保持原profile。GET/api/backends api_features.uploads=true 并公开大小/格式限制，不暴露路径/command。
- [ ] **8：验证 GREEN。** 定向测试与adapter/lifecycle目标测试全部通过；现有HTTP文本错误/CSRF格式/hostname回归不破坏，测试headers用实际host。只改server设施，不 import/shell启动任何视觉模型。
- [ ] **9：提交。** 精确stage本任务文件，`git commit -m "feat: route media attachments through selected adapters"`；D profile在部署manifest记录，不stage真实配置。

### Task 3: 选择、预览、上传与消息 UI

**Files:** 修改 F:ui/index.html、ui/style.css、ui/app.js；新增 ui/media.js、tests/test_media_frontend_browser.py；扩展原frontend/lifecycle/session测试。

**Interfaces:** `MediaComposer({onChange,onError,limits})` 在media.js中实现；`choose(kind, backendId) -> void`、`upload(file, kind, backendId) -> Promise<metadata>`、`clear({discard=true}) -> void`、`get pending() -> metadata|null`、`get uploading() -> bool`、`get hasFile() -> bool`、`dispose() -> void`。通过原生classic script在app.js之前defer加载，避免现有Chromiumfixture失配。XHR POST raw File，不使用multipart/base64；原fetch仍发送JSONchat。

- [ ] **1：写失败测试。** Chromiumfixture构造小File/Blob、stub XHR/object URL计数，验证image/video选择/uploadprogress/取消/delete/unsupported状态；空text有附件允许send，未upload完成不允许；后端切换清preview/revoke，失败保留用户消息并要求重新选附件；旧epoch/晚upload响应不得填新backend pending；130秒chatabort不会自行假设serverbusy=false。

  ```python
  self.assertIn('data-attachment-only-send-enabled="true"', dom)
  self.assertIn('data-late-upload-discarded="true"', dom)
  self.assertIn('data-user-preserved-after-error="true"', dom)
  self.assertIn('data-all-preview-urls-revoked="true"', dom)
  ```
- [ ] **2：验证 RED。** F：`python3 -m unittest discover -s tests -p test_media_frontend_browser.py -v`。
- [ ] **3：实现。** input type=file accept精确格式、一个附件，图片用img对象URL/视频video controls不autoplay；预览尺寸有界、clear移除+revoke，不在历史message长期保留Blob。XHR progress轻量文字/条，上传期间selector/reset/voice禁用；listening/recognizing/thinking不可开始附件上传。发送成功/失败都清附件状态与对象URL；用户消息显示文字与附件kind/安全文件名作为textContent，不泄露serverpath。
- [ ] **4：整合 app.js。** renderCapabilities同时看 api_features.uploads、capability/input、modelready和busy。pending attachment走扩展chat JSON，其他路径保持原text；sendMessage不再用无条件pop影响交错SSE消息，按request标记移除自己的pending，保留user。reset失败仍保留现有消息/附件，reset成功clear；applyModelState/epoch改变使pending失效。避免media.js未加载时旧浏览器fixture静默成功。
- [ ] **5：验证 GREEN。** 新媒体浏览器及全部frontend、clear失败、busyfalsefence回归通过；360px和1366px无控件覆盖，嘉宾界面不显示TinyChat/paths/env，Mock不会伪造视觉理解。
- [ ] **6：提交。** 精确stage UI/tests，`git commit -m "feat: add capability-gated media composer"`。

### Task 4: 接入示例、清理说明与全量交付

**Files:** 新增 F:examples/custom_media_adapter.py、docs/media-integration.md；修改 README.md、docs/add-a-model.md、architecture.md、model-lifecycle.md；更新D:README_UI.md、最终实机清单。

**Interfaces:** 示例继承BackendAdapter，演示image/video MediaRequest转换为远端有界上传请求；不得要求远端读取本机path，必须说明传文件字节/引用如何适配。示例不包含真实账号/密钥，不宣称兼容未测服务协议。

- [ ] **1：写失败示例测试。** tests/test_adapter_contract.py import模型无关示例，temporary小附件经fake远端transport获得带ID回复，断言不 import CUDA/ASR SDK；公开profile不会漏local_path/command。

  ```python
  self.assertIsInstance(adapter.image(MediaRequest("描述", attachment)), str)
  self.assertEqual(fake_transport.received_bytes, fixture_bytes)
  self.assertNotIn("local_path", public_metadata)
  self.assertNotIn("command", public_metadata)
  ```
- [ ] **2：验证 RED→实现。** 目标contract测试先失败；实现示例最小标准库/fake transport边界，实际HTTP字段由模型开发者按自家服务填写，不能留下可启动模型的任意command；运行目标测试GREEN。
- [ ] **3：文档。** “新增profile→实现image/video→显式注册→不改前端”，trusted-LAN/无auth边界、格式筛选≠安全解码、模型侧时长/像素/抽帧限、手动ASR顺序、180秒仅Nano、失败重试需重选附件；列TMP目录残留检查与精确owned目录人工清理，不给全局/tmp删除命令。
- [ ] **4：全量验证。** F/D分别 `python3 -m unittest discover -s tests -q`；`python3 -m py_compile ui_backend/*.py integrations/*.py ui_server.py`（F）；D相关shim+ASR compile不运行；`git diff --check`、`python3 scripts/audit_repository.py`；检查新 uploads/.partial不被Git追踪、assistant hash一致、核心无本机路径依赖。
- [ ] **5：提交与交付。** F 精确stagedocs/examples/contract tests，`git commit -m "docs: explain media adapters and safe deployment"`；最终独立只读review按索引执行，真实GPU/RAM/ASR/媒体模型仍未验收。不自动push，交付报告提供明确local commits和发布待确认状态。
