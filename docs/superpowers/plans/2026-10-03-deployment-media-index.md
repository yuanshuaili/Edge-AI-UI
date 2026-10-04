# 部署整理与多模态 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已批准规格改善启动/语音体验、统一部署 UI 来源，并交付通用媒体附件流程。

**Architecture:** 分成三个可独立验收的计划，顺序执行。Framework 源码只在既有共享 Git 仓库维护；部署只保留配置、集成和兼容入口。媒体经临时磁盘文件和 Adapter 调用，不在 UI 加载/解码模型。

**Tech Stack:** Python 3.10+ 标准库、unittest、HTML/CSS/原生 JS、已有 Chromium 测试；原推理及 ASR 环境不变。

**Spec:** `docs/superpowers/specs/2026-10-03-deployment-unification-media-design.md`（用户于本轮确认）。

## Global Constraints

- F 表示既有独立 Framework Git 根，D 表示原 Nano deployment 根；计划中文件前缀 F:/D: 是定位符，不是文件名。命令在指定根执行。
- 用户选定当前会话逐项实施、每包测试后继续、最后一次独立只读审阅；不得并行让多个代理修改共享接口。
- 正式 D:config/assistant.json 字节及 SHA256 不变；不修改 venv、权重、量化、ASR/VAD 核心和 QwenPrompter。
- 不启动模型/真实麦克风、不安装依赖；不做 TTS、token streaming、多用户、浏览器麦克风或真实多模态模型测试。
- D:llm-awq、models、awq_env、asr_env、flash-attention 保留原路径；single-active-LLM、外部进程所有权规则不变。
- 不删历史文件/缓存，只对精确白名单做可恢复归档；不移动 .codex/.agents/.aws/.git，不在 D git init。
- D:assistant.json/backends.json、archive、uploads、权重及本机配置不进入共享 Git；remote 不变，不自动 push 本阶段提交。
- 用 apply_patch 编辑源码/文档；移动/复制仅做 manifest 白名单迁移，不使用递归删除或 git reset 用户改动。

## Review Focus

1. UI/Qwen/ASR 仍运行时迁移会导致代码混用：迁移必须拒绝继续、不自动 kill（计划2 Task2）。
2. 兼容 ASR 入口导入为两个对象，patch 测试和运行失配：旧导入必须共享实现 globals（计划2 Task2）。
3. symlink 后读取中性示例身份/错误量化根：显式产品配置和安装根必须覆盖 resolve 默认路径（计划2 Task1）。
4. 附件上传期间模型换走又换回，旧请求误投：绑定 selection stamp，提交/消费都复核（计划3 Task1/2/3）。
5. 退出/TTL 清理删除正在推理文件，或 HTTP 取消释放 busy 太早：lease 与 gate 必须持续至 Adapter 返回（计划3 Task1/2）。

## 执行顺序

- [ ] **0：实施前基线。** 读取三个子计划与规格；检查 F Git 状态、D 用户改动、assistant hash、可用磁盘；运行 F/D 的 `python3 -m unittest discover -s tests -q`。失败先定位，不将 socket EPERM 当回归；需要 socket/Chromium 的命令申请受控权限，不安装替代工具。
- [ ] **0a：确认环境静止。** 只读查询真实主机 UI/Qwen/ASR 进程。若仍运行，请用户在原终端停止，在任何部署运行代码变更/迁移前暂停；不擅自停止。可继续 F 的隔离测试，但不能切换部署来源。
- [ ] **1：** [启动与语音计划](2026-10-03-startup-voice-plan.md) Task1→Task2→F/D 全量回归。
- [ ] **2：** [单份源码迁移计划](2026-10-03-source-layout-plan.md) Task1→Task2→F/D 全量回归与 manifest 校验。
- [ ] **3：** [媒体上传计划](2026-10-03-media-adapter-plan.md) Task1→Task2→Task3→Task4→F/D 全量回归。
- [ ] **4：只读审阅。** 一个独立 reviewer 检查实际 diff、规格覆盖、并发/路径/所有权/资源边界；主 agent 对重要问题先复现、再修复并回归，不派多个实现代理。
- [ ] **5：交付。** `git diff --check`、`python3 scripts/audit_repository.py`、Git 跟踪文件/最大文件、真实配置 ignored、核心本机路径扫描、assistant hash。报告 commit、无模型测试、manifest/恢复命令、唯一源码定位证据、ASR 手动启动命令及未实机验证项。

## 提交与恢复策略

F 的每个有意义任务完成后，只提交该任务明确文件；不自动 stage 用户其他改动。
D 没有根 Git，用 manifest 保存 before/after SHA256、原目标、归档目标和链接目标。
任务1首次同步 D/F 时比较源码，不以 F 覆盖 D 未核实的差异；任务2之后不再复制核心。
本计划文档可以本地 commit，运行代码在计划审阅后才实施；本阶段 remote 发布需交付时确认。
迁移后 F 源码更新须停止展示后部署，不对正在运行的实例热替换。

**状态：用户已批准并实施。** 历史步骤保留以便追溯；实际任务进度由 ledger 记录。
见 [最终实施记录](../../implementation-2026-10-04.md)。无模型测试完成，不代表硬件验收完成。
