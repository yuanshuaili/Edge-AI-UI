"""Browser behavior smoke test; uses installed Chromium but no model service."""

import json
import re
import shutil
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory


UI = Path(__file__).resolve().parents[1] / "ui"


@unittest.skipUnless(shutil.which("chromium"), "Chromium is not installed")
class FrontendBrowserTests(unittest.TestCase):
    def test_voice_mock_selection_gates_button_and_renders_answer(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        fixture = """<script>
window.EventSource = class {
  constructor() { window.voiceSource = this; this.listeners = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  emit(value) { this.listeners.voice({data: JSON.stringify(value)}); }
};
window.fetch = async (url, options={}) => {
  let value;
  if (url === "/api/assistant") value = {id:"sample",name:"新助手",subtitle:"本地助手",welcome:"你好。"};
  else if (url === "/api/backends") value = {default_backend_id:"offline",backends:[
    {id:"offline",name:"离线模型",device:"设备 A",capabilities:{text:true,voice:false,image:false,video:false},available_inputs:{text:true,voice:false,image:false,video:false}},
    {id:"mock-demo",name:"演示后端",device:"设备 B",capabilities:{text:true,voice:true,image:false,video:false},available_inputs:{text:true,voice:true,image:false,video:false}}]};
  else if (url === "/api/voice/state") value = await new Promise(resolve => {
    window.releaseOldVoiceState = () => resolve({selected_backend_id:"offline",phase:"idle",asr_connected:false,request_id:null,backend_busy:false,event_id:0});
  });
  else if (url === "/api/voice/selection") value = {selected_backend_id:"mock-demo",phase:"idle",asr_connected:true,request_id:null,backend_busy:false,event_id:2,epoch:"old-server"};
  else if (url === "/api/voice/control") value = {selected_backend_id:"mock-demo",phase:"listening",asr_connected:true,request_id:"r1",backend_busy:false};
  else value = {transport:"offline"};
  return {ok:true,status:200,json:async()=>value};
};
window.addEventListener("load", () => setTimeout(async () => {
  const button = document.getElementById("voiceButton");
  document.body.dataset.offlineVoiceDisabled = String(button.disabled);
  const selector = document.getElementById("backendSelect");
  selector.value = "mock-demo";
  selector.dispatchEvent(new Event("change"));
  await new Promise(resolve => setTimeout(resolve, 100));
  window.releaseOldVoiceState();
  await new Promise(resolve => setTimeout(resolve, 50));
  document.body.dataset.selectedAfterOldState = selector.value;
  document.body.dataset.mockVoiceEnabled = String(!button.disabled);
  document.body.dataset.voiceLabel = document.getElementById("voiceButtonLabel")?.textContent || "";
  window.voiceSource.emit({type:"state",selected_backend_id:"mock-demo",phase:"idle",asr_connected:false,request_id:null,backend_busy:false});
  document.body.dataset.disconnectedLabel = document.getElementById("voiceAvailability")?.textContent || "";
  document.body.dataset.disconnectedDisabled = String(button.disabled);
  window.voiceSource.emit({type:"state",selected_backend_id:"mock-demo",phase:"idle",asr_connected:true,request_id:null,backend_busy:false});
  button.click();
  await new Promise(resolve => setTimeout(resolve, 100));
  window.voiceSource.emit({type:"transcript_ready",request_id:"r1",backend_id:"mock-demo",text:"你好"});
  window.voiceSource.emit({type:"thinking",request_id:"r1",backend_id:"mock-demo"});
  window.voiceSource.emit({type:"answered",request_id:"r1",backend_id:"mock-demo",text:"演示后端[mock-demo]已收到：你好",latency_ms:10});
  document.body.dataset.user = document.querySelector(".message.user .message-body")?.textContent || "";
  document.body.dataset.answer = document.querySelector(".message.assistant .message-body")?.textContent || "";
  document.body.dataset.imageDisabled = String(document.getElementById("imageButton").disabled);
  window.voiceSource.emit({type:"state",id:0,event_id:0,epoch:"new-server",
                           selected_backend_id:"offline",phase:"idle",asr_connected:false,
                           request_id:null,backend_busy:false});
  document.body.dataset.selectedAfterRestart = selector.value;
}, 150));
</script>"""
        html = html.replace('<script src="/app.js" defer></script>', fixture + '<script src="/app.js" defer></script>')

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    body, content_type = html.encode(), "text/html"
                elif self.path in ("/app.js", "/style.css"):
                    body = (UI / self.path[1:]).read_bytes()
                    content_type = "text/javascript" if self.path.endswith(".js") else "text/css"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, TemporaryDirectory() as profile_dir:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([
                    shutil.which("chromium"), "--headless", "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage", "--disable-background-networking",
                    "--virtual-time-budget=2500", "--dump-dom", f"--user-data-dir={profile_dir}",
                    f"http://127.0.0.1:{server.server_port}/",
                ], capture_output=True, text=True, timeout=30)
            finally:
                server.shutdown()
                thread.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        body = re.search(r"<body\b([^>]*)>", result.stdout)
        self.assertIsNotNone(body)
        self.assertIn('data-offline-voice-disabled="true"', body.group(1))
        self.assertIn('data-mock-voice-enabled="true"', body.group(1))
        self.assertIn('data-voice-label="语音输入"', body.group(1))
        self.assertIn('data-disconnected-label="语音服务未连接"', body.group(1))
        self.assertIn('data-disconnected-disabled="true"', body.group(1))
        self.assertIn('data-selected-after-old-state="mock-demo"', body.group(1))
        self.assertIn('data-user="你好"', body.group(1))
        self.assertIn('data-answer="演示后端[mock-demo]已收到：你好"', body.group(1))
        self.assertIn('data-image-disabled="true"', body.group(1))
        self.assertIn('data-selected-after-restart="offline"', body.group(1))

    def test_error_keeps_user_message_and_profile_updates_accessible_labels(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        fixture = """<script>
window.fetch = async (url) => ({
  ok: url !== "/api/chat", status: url === "/api/chat" ? 503 : 200,
  json: async () => url === "/api/assistant"
    ? {id:"sample",name:"新助手",subtitle:"本地助手",welcome:"你好，我是新助手。",}
    : url === "/api/backends"
      ? {default_backend_id:"test",backends:[{id:"test",name:"示例模型",device:"测试设备",mode:"Local",
          capabilities:{text:true,voice:false,image:false,video:false,tts:false},
          available_inputs:{text:true,voice:false,image:false,video:false}}]}
      : url === "/api/chat" ? {error:{message:"模型离线"}} : {transport:"offline"},
});
window.addEventListener("load", () => setTimeout(() => {
  const input = document.getElementById("messageInput");
  input.value = "不能丢的提问";
  input.dispatchEvent(new Event("input", {bubbles:true}));
  document.getElementById("chatForm").requestSubmit();
  setTimeout(() => {
    document.body.dataset.userText = document.querySelector(".message.user .message-body")?.textContent || "";
    document.body.dataset.noticeText = document.querySelector(".message.notice .message-body")?.textContent || "";
    document.body.dataset.brandLabel = document.querySelector(".brand")?.getAttribute("aria-label") || "";
    document.body.dataset.conversationLabel = document.querySelector(".conversation")?.getAttribute("aria-label") || "";
    document.body.dataset.inputLabel = document.querySelector('label[for="messageInput"]')?.textContent || "";
  }, 250);
}, 250));
</script>"""
        html = html.replace('<script src="/app.js" defer></script>', fixture + '<script src="/app.js" defer></script>')

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    body = html.encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                elif self.path in ("/app.js", "/style.css"):
                    body = (UI / self.path[1:]).read_bytes()
                    content_type = "text/javascript" if self.path.endswith(".js") else "text/css"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, TemporaryDirectory() as profile_dir:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([
                    shutil.which("chromium"), "--headless", "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage", "--disable-background-networking",
                    "--virtual-time-budget=2500", "--dump-dom", f"--user-data-dir={profile_dir}",
                    f"http://127.0.0.1:{server.server_port}/",
                ], capture_output=True, text=True, timeout=30)
            finally:
                server.shutdown()
                thread.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        body = re.search(r"<body\b([^>]*)>", result.stdout)
        self.assertIsNotNone(body, result.stdout[-800:])
        self.assertIn('data-user-text="不能丢的提问"', body.group(1))
        self.assertIn('data-notice-text="模型离线"', body.group(1))
        self.assertIn('data-brand-label="新助手', body.group(1))
        self.assertIn('data-conversation-label="与新助手对话"', body.group(1))
        self.assertIn('data-input-label="给新助手发消息"', body.group(1))


if __name__ == "__main__":
    unittest.main()
