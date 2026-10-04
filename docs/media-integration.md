# 图片与视频接入

UI 不加载视觉模型、不抽帧、不解码视频。新增模型需要服务端 Adapter，
而不是修改前端。当前 `mock-media` 只验证路径，明确回答“模拟，未分析媒体内容”。
文本模型的 image/video 能力必须保持 false。

## 最小接入

1. 从 backends.example.json 中复制 mock-media profile，填写你的名称、设备、adapter 与 endpoint。
2. 继承 BackendAdapter，实现 health/text_chat，以及需要的 image(MediaRequest) / video(MediaRequest)。
3. 在 adapter_registry.py 显式注册类；JSON 不接受模块路径或任意命令。
4. 将实际已实现的 capability 与 available_inputs 设为 true；缺少方法会拒绝启动配置。
5. external 服务由你独立启动；managed 模型另注册服务器白名单 Launcher。
6. 页面自动显示并启用对应按钮，不改 index.html / app.js / style.css。

参考 examples/custom_media_adapter.py。Attachment.local_path 是 UI 机器上的私有文件，
远端模型不能直接读取它：Adapter 必须传文件字节或使用自家服务的上传/引用协议。
示例 transport 同步消费 64 KiB 字节块；没有声称兼容任何未测试的厂商 API。
实际 transport 需设置总超时、大小限制、响应上限、鉴权和正确 schema，
真实 health 应使用短且有界的探测。不要把密钥放在共享配置或浏览器。

## 协议

- POST /api/attachments?kind=image|video&backend_id=ID：raw binary，唯一 Content-Length。
- 返回 attachment_id/kind/media_type/size/expires（Unix 时间），无本机路径。
- POST /api/chat：`{"backend_id":"ID","text":"","attachment_id":"opaque ID"}`。
- DELETE /api/attachments/ID：删除未占用附件，不能删除推理期间的 lease。
- 无附件仍走 text_chat；语音 transcript 也仍走同一文字 dispatcher。

一次一附件。上传与消费绑定 backend + server epoch + lifecycle revision + conversation revision。
换走再换回同模型也不会复用旧附件。新对话成功、切换模型会使待发附件失效。
发送失败保留用户消息；重试必须重新选择附件。取消 HTTP 不意味着中止模型；
busy/lease 保留到 Adapter 完成，不能在超时后立即启动另一轮推理。
媒体 lease 从协调器统一准入后才获取；采集、识别、清空、模型切换与媒体准入互斥。
新对话成功递增 conversation revision，即使旧附件已被外部测试占用，也不能投到新对话。

## 资源与边界

默认图10 MiB、视频50 MiB、总100 MiB、16个记录、15分钟闲置 TTL、30秒上传总 deadline。
每块64 KiB、一个上传、一个媒体 lease、一个清理线程；不创建无界队列。
服务端 CLI 可以调整 `--max-image-mib`、`--max-video-mib`、`--attachment-quota-mib`、
`--max-attachments`、`--attachment-ttl-seconds`、`--upload-timeout-seconds`、
`--attachment-parent-dir`。硬上限单文件512 MiB、总1 GiB、128记录、TTL24小时、上传120秒。

只支持 JPEG/PNG/WebP、MP4/WebM，有限文件头筛选不是安全/完整解码验证。
模型服务必须额外限制像素、帧数、时长、解码资源、抽帧率和请求总时长；
UI 不保证恶意媒体对第三方解码器安全。拒绝 SVG/HTML/未知类型，不托管原始媒体。
目录0700、文件0600、随机名字，不能提交 path/URL/base64/command。

这是可信本机/局域网演示服务，Host/Origin 检查不是用户认证；不要直接暴露公网。
需要公网部署时先增加认证、授权、TLS 与隔离解码等边界。

## 退出与残留

正常退出取消上传、等待有界在途请求（最多130秒）、不删除被 Adapter 占用的文件，
完成后释放 lease 并清理本实例目录。异常退出可能留下 `edge-ai-attachments-*` 目录。
先用 `ps` 检查没有对应 UI/Adapter 在使用，再核对目录所有者、0700权限及确切路径，
按实例逐个人工处理。不要递归删除整个 /tmp，也不要在占用期间清理文件。

## 无模型验收

用 mock-media 测 JPEG/PNG/WebP/MP4/WebM 的选择、上传、预览、移除、空文字发送、
错误后消息保留、能力切换及新对话。Mock 回复只证明路由，不证明视觉理解。
真实视觉模型的输出质量与 RAM/GPU 峰值由接入者另行验收。
