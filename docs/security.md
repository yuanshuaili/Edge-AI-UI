# 安全边界与接入责任

## 支持的使用场景

本 Framework 是单用户、本机或可信局域网的展示服务，不是经过认证的多租户 Web
产品。默认只监听 loopback；公开网络部署需另做认证、授权、TLS、访问限制、解码
隔离和安全运维。Host/Origin 校验不等于认证，也不能阻止可直接访问接口的网络客户端。
同一 OS 用户的其他程序和服务器端注册的 Python 代码属于可信边界。

## 现有防护

- 浏览器通过固定 API 发送 backend_id/文字/opaque attachment_id，不提交 shell command、
  Python module、文件 path 或媒体 URL。Adapter 和 Launcher 必须在服务器字典显式注册。
- 模型答案、用户文本、名称和文件提示不作为 HTML 执行。静态资源只来自固定列表。
- API 校验 Origin/Fetch Metadata，事件流另需同源证明或页面 nonce，避免无 Origin
  的 no-cors 浏览器请求长期占用资源。SSE 最多 4 个独立槽位，不占普通 API 配额。
  CSP frame-ancestors 'none' / X-Frame-Options DENY 阻止其他网站嵌入控制界面。
- JSON、转写、socket frame、HTTP 客户端、SSE 留存与 lifecycle worker 有边界。
  JSON 请求体校验确切字节长度并使用总接收 deadline，不仅依赖 socket idle timeout。
  错误不删除已展示的用户消息；超时不把真实未结束的模型调用伪装成空闲。
- ModelManager 只停止自己拥有的进程，启动新受管模型前检查旧组和 endpoint 已释放。
  不确定 endpoint 是否释放时 fail closed；不执行 shell=True。
  UI 退出等待 HTTP、媒体 lease 和后台语音推理，最多 130 秒；未完成时保留 owned
  模型资源并记录日志，不在 busy 时强行销毁。第三方 Adapter 的总推理 deadline 仍必需。
- 附件固定长度上传、总时间/大小/数量/配额限制；随机私有目录 0700、文件 0600，
  独占创建，客户端无法选择保存路径。媒体消费使用单次 lease，切换/新对话使旧附件失效。
- 文件头筛选拒绝 SVG/HTML/未知类型，上传不是公开文件托管服务。UI 本身不解码媒体。
- 本机 Profile、权重、venv、缓存、上传文件、日志和常见密钥文件不应被 Git 跟踪。
  scripts/audit_repository.py 是有限的启发式检查，不是完整 secret scanner 或渗透测试。

## 接入者必须完成

1. Adapter 构造时验证 endpoint schema，不接受嘉宾指定的远端 URL。
2. 所有 transport 设置连接/读/总 deadline、输入输出大小限制和有界资源。UI 不会
   强杀卡住的 Python Adapter 线程；没有 total deadline 的第三方 SDK 会妨碍结束 busy。
3. credentials 只放服务端受保护配置/环境。转成 BackendError 的文案可显示给浏览器，
   所以不要包含 credential、私有 URL、模型路径、traceback 或供应商错误原文。
4. 模型服务在真正可推理后才 health ready；受管服务实现准确 endpoint_occupancy。
   不要 daemonize 或让子进程脱离原进程组；不要在 UI 中加载模型。
5. 多模态服务做完整格式验证、像素/帧数/时长/抽帧/解码/模型内存限额。不可信媒体
   应隔离解码；有限 magic-byte 检查不保证恶意文件对第三方解码器安全。
6. 媒体必须同步消费完成后再返回 Adapter 调用，不能在 lease 释放后异步读取文件。
7. 推理服务自行设置 Assistant Profile system message，clear_session 保留身份。
   Prompt 不是硬身份/安全策略，模型可能不遵循；需要单独验收身份和敏感信息策略。
8. 若面向多个访客终端，需要认证和会话隔离；当前一个共享会话，SSE 不按用户隔离。

## 发布前检查

```bash
python3 -m unittest discover -s tests -v
python3 scripts/audit_repository.py
git diff --check
git status --short
git ls-files
```

检查将要发布的提交历史以及当前文件，不只检查 .gitignore。不要提交真实配置、
模型权重、录音/访客上传、token、SSH key 或 Codex 配置。若密钥曾提交，应撤销密钥
并专门处理历史；新加 .gitignore 不能清除旧历史。

自动化覆盖有界上传、错误/取消、Expect:100-continue、生命周期/ownership、HTTP/socket
和浏览器交互，但不意味着不存在漏洞。公网防御、真实 VLM 解码、第三方 Adapter/SDK
和内容安全不在这些无模型测试的证明范围内。
