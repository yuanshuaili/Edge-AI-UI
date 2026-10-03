"""Actual browser reset success, failure and busy gating; no inference model."""
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
class SessionBrowserTests(unittest.TestCase):
    def test_reset_keeps_messages_on_failure_and_restores_welcome_on_success(self):
        fixture = """<script>
window.EventSource = class { constructor(){window.events=this;} addEventListener(name,fn){if(name==='voice')this.fn=fn;}
  emit(value){this.fn({data:JSON.stringify(value)});} };
window.fetch = async (url, options={}) => {
  let value, ok=true;
  if (url === '/api/assistant') value={id:'a',name:'演示助手',subtitle:'本地助手',welcome:'欢迎开始。'};
  else if (url === '/api/backends') value={default_backend_id:'m',backends:[{id:'m',name:'演示模型',device:'边缘设备',
    capabilities:{text:true,voice:true,image:false,video:false},available_inputs:{text:true,voice:true,image:false,video:false}}]};
  else if (url === '/api/voice/state') value={phase:'idle',asr_connected:true,backend_busy:false};
  else if (url === '/api/chat') value={text:'旧回答',latency_ms:1};
  else if (url === '/api/session/clear') {
    const button=document.getElementById('newConversation');
    document.body.dataset.resetBusy=String(button.disabled);
    document.body.dataset.micBusy=String(document.getElementById('voiceButton').disabled);
    if (!window.allowReset) { ok=false; value={error:{message:'未清空，请重试'}}; }
    else {
      window.events.emit({type:'backend_busy',id:8,epoch:'fixture',backend_id:'m',busy:true});
      value={ok:true,backend_id:'m',event_id:9,epoch:'fixture'};
      setTimeout(()=>window.events.emit({type:'backend_busy',id:9,epoch:'fixture',backend_id:'m',busy:false}),50);
    }
  } else value={transport:'reachable'};
  return {ok,status:ok?200:503,json:async()=>value};
};
window.addEventListener('load',()=>setTimeout(async()=>{
  const pause=()=>new Promise(r=>setTimeout(r,100));
  const input=document.getElementById('messageInput');
  input.value='必须保留的提问'; input.dispatchEvent(new Event('input'));
  document.getElementById('chatForm').requestSubmit(); await pause();
  document.getElementById('newConversation').click(); await pause();
  document.body.dataset.failureUser=document.querySelector('.message.user .message-body')?.textContent;
  document.body.dataset.failureAnswer=document.querySelector('.message.assistant .message-body')?.textContent;
  document.body.dataset.failureNotice=document.querySelector('.message.notice .message-body')?.textContent;
  window.allowReset=true; document.getElementById('newConversation').click(); await pause();
  document.body.dataset.messageCount=String(document.querySelectorAll('.message').length);
  document.body.dataset.welcome=document.getElementById('welcomeText').textContent;
  document.body.dataset.clearRecovered=String(!document.getElementById('newConversation').disabled);
  document.body.dataset.inputRecovered=String(!document.getElementById('messageInput').disabled);
},250));
</script>"""
        html = (UI / "index.html").read_text().replace(
            '<script src="/app.js" defer></script>', fixture + '<script src="/app.js" defer></script>')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    body = html.encode()
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
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, TemporaryDirectory() as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([
                    shutil.which("chromium"), "--headless", "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage", "--disable-background-networking",
                    "--virtual-time-budget=2500", "--dump-dom", f"--user-data-dir={directory}",
                    f"http://127.0.0.1:{server.server_port}/",
                ], capture_output=True, text=True, timeout=30)
            finally:
                server.shutdown()
                thread.join(2)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        body = re.search(r"<body\b([^>]*)>", result.stdout)
        self.assertIsNotNone(body)
        for expected in ('data-failure-user="必须保留的提问"', 'data-failure-answer="旧回答"',
                         'data-failure-notice="未清空，请重试"', 'data-message-count="0"',
                         'data-reset-busy="true"', 'data-mic-busy="true"', 'data-welcome="欢迎开始。',
                         'data-clear-recovered="true"', 'data-input-recovered="true"'):
            self.assertIn(expected, body.group(1))
