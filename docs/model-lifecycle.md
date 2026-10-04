# Model lifecycle and ownership

States: offline → starting → ready → stopping → offline. Activation of another
backend uses switching; failures end in error. Poll GET /api/model/state or use
the model_state messages on existing /api/voice/events SSE. State contains safe
IDs, ownership boolean, revision/epoch and neutral error_code; never commands/PIDs.

POST /api/model/activate requires exactly {"backend_id":"configured-id"}.
POST /api/model/stop requires {}. Returns 202 for accepted asynchronous work;
clients must wait for final ready/offline/error rather than treating 202 as ready.
Unknown IDs, commands or extra fields are rejected. One bounded lifecycle worker;
no queued simultaneous model starts. busy/clear/chat reject transitions with 409.

Switch order: block dispatch/cancel capture → stop old owned group → SIGTERM →
wait → SIGKILL only on timeout → reap child → confirm no live group descendants →
confirm old endpoint offline → invoke new known launcher → wait usable health →
commit selected backend → resume UI. Failure never preloads a second model and
never performs automatic rollback. Developer logs are under logs/model-ID.log;

Endpoint occupancy is checked separately from health. Only a definite absence
(for TCP, connection refused) allows loading the next model. A timed-out/saturated
listener or indeterminate probe blocks launching rather than guessing offline.
Process-group numeric authority is retired after departure; leader start identity
is checked to avoid signalling a reused PID after an unexpected exit.
details are not guest-facing. stdout/stderr are directed to disk, not buffered in RAM.

No ready unowned managed endpoint may coexist with a newly spawned managed LLM.
UI can connect to a manually started matching service without adopting its process;
it will refuse to stop it or launch a different managed model until its original
owner stops it. External backends are always connection-only. The manager's
single-active guarantee covers its own processes and declared managed endpoints,
not arbitrary models launched elsewhere without being declared.

Normal Ctrl+C/SIGTERM UI exit stops its owned process, never external/manual
services. Hard-killed UI cannot run cleanup. Models must not daemonize, create a
new session, or detach from the group; such launchers violate the integration
contract. Undeclared services, hard kill and power loss require manual inspection.

Mode A: default managed runtime auto_start false; select it or click 启动模型.
Mode B: default managed runtime auto_start true; UI starts it on startup (exhibition
recommended after hardware acceptance). Sample config is Mock-only; actual Nano
development keeps false to avoid unintended Qwen loading.

Optional Nano launcher tinychat_qwen25 always uses Qwen2.5-3B-Instruct W4A16/g128,
the existing quantized file, separate awq_env Python, no FP16 UI option. Set
EDGE_AI_TINYCHAT_ROOT to your installation root. This integration expects its
existing loopback endpoint; that endpoint is an integration contract, not a
generic framework-core requirement. RAM/GPU release and real Qwen startup need
hardware acceptance; unit tests do not measure GPU allocator behavior.
## 部署参数和冷启动观测

--assistant-config 与 --model-log-dir 明确选择部署身份和日志。
Nano 启动窗口建议180秒（通用默认60秒），没有自动重试或并行预加载。
日志区分 attempt_id、backend_id、elapsed_seconds、ready/exit/timeout 与清理结果。
Nano Python 使用 -u；部署 llm_server 输出导入完成、配置/tokenizer、权重、
设备预热、模型预热、socket ready 时间戳。日志不是实际冷启动验收的替代。
