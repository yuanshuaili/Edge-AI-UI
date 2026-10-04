# 启动窗口与语音提示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 减少 60 秒边界误终止并使语音入口和服务未连接原因可见。

**Architecture:** 保留原 lifecycle；Nano 调整启动窗口，开发日志增加 attempt/阶段时间。Browser 只展示语音可用性，不管理 ASR 进程。

**Tech Stack:** 标准库 logging/time、现有 unittest 和 Chromium；不加载模型。

**Spec:** `docs/superpowers/specs/2026-10-03-deployment-unification-media-design.md` 工作包一。

## Global Constraints

继承索引 Global Constraints。D/F 含义同索引。Nano startup=180 秒、通用 runtime 默认仍60秒；shutdown 原10秒；无自动重试/第二模型；ASR 仍手动启动一份。

## Review Focus

1. 失败重试日志混在一起，无法区分：每个 attempt 有随机 ID，阶段日志有时间戳。
2. stdio 缓冲导致阶段日志不可见：Nano Python argv 用 `-u`，量化参数不改。
3. early exit 与 startup timeout 被混称：错误 code 与原 cleanup 顺序分别保留。
4. ASR 已连接但 model starting/busy，提示误称未连接：提示按状态明确优先级。
5. 低宽视口“语音输入”标签挤掉发送：Chromium 测移动和桌面宽度，不扩大视觉效果。

### Task 1: 启动窗口和开发观测

**Files:** 修改 F:ui_backend/model_manager.py、F:integrations/tinychat_nano.py；迁移前核对后同步对应 D 文件。修改 D:config/backends.json、D:llm-awq/tinychat/llm_server.py。扩展 F:tests/test_model_manager.py；新增 D:tests/test_startup_diagnostics.py。

**Interfaces:** `ModelManager._start_owned(profile)` 外部行为不变；日志带 `attempt_id/backend_id/elapsed_seconds/outcome`。llm_server 新增 `log_startup_stage(stage: str, started_at: float) -> None`，输出开发时间戳与 elapsed；调用点不改加载执行顺序。

- [ ] **1：写失败测试。** F 中增加 `test_attempt_logs_distinguish_ready_timeout_and_exit`，用现有 CPU-only fixture 断言三种 outcome 与 attempt ID、elapsed>=0；`test_nano_launcher_is_unbuffered_and_fixed_quantization` 断言 argv 含 `-u/W4A16/128`。D 测试断言 Nano profile180、RuntimeConfig 默认60、other profile 不变，并 AST 检查阶段调用，不 import 真实 llm_server。

  ```python
  self.assertEqual(nano.runtime.startup_timeout_seconds, 180)
  self.assertEqual(RuntimeConfig().startup_timeout_seconds, 60)
  self.assertIn("-u", spec.argv)
  self.assertIn("W4A16", spec.argv)
  self.assertEqual(spec.argv[spec.argv.index("--q_group_size") + 1], "128")
  ```
- [ ] **2：验证 RED。** F：`python3 -m unittest discover -s tests -p test_model_manager.py -v`；D：`python3 -m unittest discover -s tests -p test_startup_diagnostics.py -v`。新断言失败应仅因日志/180/-u 尚未存在，不因缺少权重。
- [ ] **3：最小实现。** Nano profile 只改 timeout。保留旧 model log 路径以兼容文档，append 模式增加 attempt 起止分隔；launcher stdout 无缓冲。llm_server 在第三方 imports 之后记录 imports complete，再记录 config/tokenizer、weights、device warmup、model warmup、socket ready；不得改 GPU/模型/量化调用或增加额外预热。
- [ ] **4：验证 GREEN。** 重跑两命令，检查 startup timeout 后 group/endpoint 释放测试仍通过；`python3 -m py_compile ui_backend/model_manager.py integrations/tinychat_nano.py`，D 只编译 llm_server、不执行。
- [ ] **5：提交。** F 只 stage manager/integration/test 文件，`git commit -m "fix: improve startup timing diagnostics"`；D config/server 保存哈希记录，不在 nested repo 混入用户改动。

### Task 2: 可见语音入口与精确提示

**Files:** 修改 F:ui/index.html、ui/app.js、ui/style.css、docs/voice-integration.md；扩展 F:tests/test_ui_frontend_browser.py、tests/test_lifecycle_frontend.py；更新 D:README_UI.md。部署静止且核对差异后，迁移前同步对应 D UI 文件。

**Interfaces:** 保留 `voiceButton` ID、toggleVoice、SSE 事件；新增 `voiceButtonLabel` 和 `voiceAvailability` 文案元素。`renderVoiceControls()` 同时更新标签、提示和原门控，不启动进程。

- [ ] **1：写失败浏览器测试。** 断言 ASR false 时控件显示“语音输入”/“语音服务未连接”且 disabled；ASR true/ready 时启用；starting 与 backend_busy 显示等待而非连通可用；listening 显示取消且可再次点击。360px/1366px 下按钮区域不被发送控件遮挡，aria-label 保留。

  ```python
  self.assertIn('data-voice-label="语音输入"', dom)
  self.assertIn('data-disconnected-label="语音服务未连接"', dom)
  self.assertIn('data-disconnected-disabled="true"', dom)
  self.assertIn('data-connected-enabled="true"', dom)
  self.assertIn('data-starting-disabled="true"', dom)
  ```
- [ ] **2：验证 RED。** F：`python3 -m unittest discover -s tests -p test_ui_frontend_browser.py -v` 和 `-p test_lifecycle_frontend.py -v`；沿用已安装 Chromium，无需新增浏览器依赖。
- [ ] **3：实现。** 标签始终可见；提示优先级为不支持→未接入→ASR未连接→模型未ready/transition→busy/reset→可点击；原 capture/recognizing/thinking feedback 保留。取消仍需 valid request_id；不通过 capability 独自启用。HTML 不再标“即将接入”。文档说明设备侧麦克风和 UI→Qwen ready→ASR UI mode 的手动顺序。
- [ ] **4：验证 GREEN。** 重跑定向浏览器测试；F/D：`python3 -m unittest discover -s tests -q`，原 CLI/voice protocol、取消、20 秒无语音回归全部保留。不宣称真实声卡已测试。
- [ ] **5：提交。** F 只 stage本任务 UI/docs/tests，`git commit -m "fix: make voice availability visible"`；检查 D 正式 assistant hash 不变。
