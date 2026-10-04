# 单份 UI 源码与可恢复部署整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nano 和共享仓库实际使用同一份 UI 代码，目录分类不破坏旧命令/导入。

**Architecture:** F 为唯一 Git/UI 源码，D 有入口 wrapper 和显式部署参数，兼容 import 链接至 F。ASR/工具归类、历史文件归档，每一项经 manifest 验证，不移动依赖/权重。

**Tech Stack:** Python pathlib/importlib/runpy 标准库，POSIX symlink，现有 unittest；不新增安装。

**Spec:** `docs/superpowers/specs/2026-10-03-deployment-unification-media-design.md` 工作包二。

## Global Constraints

继承索引 Global Constraints。不在线迁移，不自动 kill，不在 D git init，不删除缓存/历史；F 路径仍是已有 Git repo，不能让部署永久指向临时工作树。

## Review Focus

1. module.__file__.resolve 后读取 F 的中性身份：--assistant-config 明确传 D 配置并测实际 public identity。
2. cwd 变化让 quant/log 根漂移：Nano 安装根通过 EDGE_AI_TINYCHAT_ROOT，日志有显式参数。
3. ASR shim 与测试 patch 不是同一 globals：转发必须让公开函数/常量保留原可 patch 行为。
4. 归档中断/已有目标冲突丢失文件：manifest + no-clobber +逆序 rollback，遇未匹配 hash 停下。
5. benchmark 原测试读取源码字符串而非执行接口：相应旧路径采用实现 symlink，不用缺少参数字面量的空 wrapper。

### Task 1: 消除隐式产品路径

**Files:** 修改 F:ui_server.py、ui_backend/server.py；新增 F:tests/test_entrypoint_config.py；更新 F:README.md、docs/model-lifecycle.md。部署 wrapper 在 Task2 新建。

**Interfaces:** `main(argv: list[str] | None = None) -> None`；CLI 新增 `--assistant-config Path`、`--model-log-dir Path`。`create_server(..., assistant_path, model_log_dir)` 使用已有参数，不更名。默认 standalone 配置路径保持 F:config；部署入口显式传 D 配置/日志。

- [ ] **1：写失败测试。** 构建中性 F 临时目录和不同名称 D 临时 profile，Mock/auto_start=false；断言参数选择 D assistant/backend/log，未知参数失败，默认依旧 standalone。不得加载正式 prompt 权重；正向用临时文本 profile。

  ```python
  self.assertEqual(server_args["assistant_path"], deploy / "config/assistant.json")
  self.assertEqual(server_args["model_log_dir"], deploy / "logs")
  self.assertEqual(profile.name, "部署测试助手")
  ```
- [ ] **2：验证 RED。** F：`python3 -m unittest discover -s tests -p test_entrypoint_config.py -v`。
- [ ] **3：实现。** 新 CLI 选项只服务端解析。`main(argv)` 可测，把路径明确传 create_server；不添加任意模块/command 参数，不泄露 private prompt。README 明确 deployment/profile 与分享 example 的区别。
- [ ] **4：验证 GREEN。** 定向测试和 F 全套通过；`python3 ui_server.py --help` 只帮助输出不 create server/load model。记录 launcher 环境 `EDGE_AI_TINYCHAT_ROOT` 的相对/绝对 root 解算测试。
- [ ] **5：提交。** F 精确文件提交 `feat: support explicit deployment paths`。

### Task 2: manifest、兼容入口与目录切换

**Files:** D 新增 `tools/deployment_layout.py`、`tests/test_deployment_layout.py`、`docs/deployment-layout.md`；D 改 root `ui_server.py`、root `asr_tinychat.py`；ASR 实现移入 `asr/asr_tinychat.py`、`asr/vad_asr.py`、`asr/asr_test.py`。其余文件按下方白名单归类；F README 链接说明、不收纳 deployment 工具/权重。

**Interfaces:** `build_manifest(deploy_root: Path, framework_root: Path) -> dict`、`apply_manifest(manifest: dict) -> None`、`rollback_manifest(manifest: dict) -> None`，CLI `--dry-run/--apply/--rollback manifest.json`。manifest v1 包含 source/destination/hash/kind、link target、completed 状态及 protected roots；不从 Browser 调用。

- [ ] **1：写失败测试。** 临时树中验证同内容 F/D 三模块可归档+链接；不一致拒绝覆盖；任何现存目标拒绝；一次操作失败逆序恢复；源/链接被用户修改则 rollback 拒绝。stub live-process 检查返回 running 时不动文件。对 ASR 旧入口的 patched ask_tinychat/on_recognizing 行为同现有测试；benchmark旧源码可读取原参数字面量。

  ```python
  self.assertEqual((deploy / "ui_backend/server.py").resolve(), framework / "ui_backend/server.py")
  rollback_manifest(manifest)
  self.assertEqual(after_hashes, original_hashes)
  self.assertFalse((deploy / "ui_backend").is_symlink())
  ```
- [ ] **2：验证 RED。** D：`python3 -m unittest discover -s tests -p test_deployment_layout.py -v`。测试全用 TemporaryDirectory，无模型/audio 访问。
- [ ] **3：实现 migration helper。** 固定 whitelist，不 glob 删除；manifest JSON 用 apply_patch 维护真实记录或 helper 的正常应用输出生成。拒绝权重/venv/敏感 roots。仅在真实主机进程检查静止后应用，歧义进程路径 fail closed；检查精确真实目录、拒绝源 symlink 越界，不读取凭据。
- [ ] **4：实现根 UI wrapper。** 通过可信相邻 `edge-ai-ui` 路径加载 F 的入口，默认补 `--config D/config/backends.json --assistant-config D/config/assistant.json --model-log-dir D/logs`；保留用户传入的 host/port/asr-socket/监听期限。设置服务端安装根 `EDGE_AI_TINYCHAT_ROOT` 为 D（若显式配置已有值先核对，不覆盖冲突）；不加载模型。
- [ ] **5：实现 ASR 兼容。** 移入 asr 前保存原脚本；根 shim 用实现源 compile/exec 到当前 module globals 的同一 namespace，编译 filename 和该namespace的 __file__ 指向真实实现，确保 monkeypatch/默认 __main__ 行为保留；root CLI 参数行为不变。实现脚本不再硬编码安装根，用可配置 EDGE_AI_ASR_ROOT 或其相邻 deployment root。ASR实现自身将可信deployment root加入sys.path，保证直接运行新路径也能导入单份ui_backend。vad/asr_test 可直接在新目录运行，保留旧路径实现链接。避免双套核心复制。
- [ ] **6：白名单归类并 dry-run。** 将 ui/ui_backend/integrations/旧 framework 模板归档到 D:archive/<timestamp>/，留原哈希；D:edge-ai-ui→F、旧三模块路径链接到 F 实现。benchmark_deepseek_qwen.py、其测试、inspect 两脚本、qwen25_awq_demo.py、fp16_stream_demo.py 核对 cwd 后进 tools，保留旧实现链接；不运行可能 import/load 模型的工具。
- [ ] **7：归档历史并记录。** 根 .before_*、历史 patch、实验 .log、status_before_7b.txt、展示截图分 archive 组；*.wav 作为 samples/ 归类并保留旧实现路径链接。对本次未列明的文件默认保持原位；SenseVoice/Silero/模型/datasets/autoawq_test/flash-attention 均不以“未使用”删除。manifest 明确 linked/moved/retained/archived。
- [ ] **8：真实迁移。** 再检查没有 live UI/Qwen/ASR、assistant hash 和 F/D 当前差异；若不能确认静止，暂停请用户关闭。先 dry-run 输出精确目标，核对后 apply；每项成功更新 manifest，异常立即 rollback 已完成项，遇 hash mismatch 保留证据并请求方向，不覆盖用户新改动。
- [ ] **9：验证。** D：`python3 -m unittest discover -s tests -q`；F 同命令。D 根 UI/ASR --help（ASR 用现有 asr_env，帮助不初始化识别器），CLI 模式 patch回归。`Path(D/ui_backend/server.py).resolve()` 等于 F 实现；三模块来源唯一；profile hash不变。UI --help 或 CPU Mock临时配置不得启动真实 default auto_start backend。
- [ ] **10：文档与提交。** 写 manifest 路径、每组移入/保留理由、人工 `--rollback` 步骤及启动命令；F 只提交共享 README 改动，D 记录 changes，不根 git init。若后续要回滚源码，链接目标需原始 snapshot，不把当前 F 错当原版本。

**恢复验收:** 临时树真实 apply→rollback 后 sha256/布局与初始完全一致；真实 D 恢复操作仅在失败需要时执行，且保护用户后续改动。归档不会上传 GitHub。
