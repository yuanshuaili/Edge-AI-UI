"""Frontend transport failures stay localized without discarding conversation."""
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"


@unittest.skipUnless(shutil.which("chromium"), "Chromium is not installed")
class TransportBrowserTests(unittest.TestCase):
    def test_network_failure_localizes_chat_and_clear_without_losing_messages(self):
        fixture = """<script>
window.EventSource=class {addEventListener(){} close(){}};
window.fetch=async(url)=>{
 if(window.abortChat&&url==='/api/chat')throw new DOMException('aborted','AbortError');
 if(['/api/chat','/api/session/clear'].includes(url))throw new TypeError('Failed to fetch');
 const value=url==='/api/assistant'?{id:'a',name:'新助手',subtitle:'本地',welcome:'欢迎'}
  :url==='/api/backends'?{default_backend_id:'m',backends:[{id:'m',name:'示例模型',device:'边缘设备',
    capabilities:{text:true,voice:false,image:false,video:false},available_inputs:{text:true,voice:false,image:false,video:false}}]}
  :url==='/api/voice/state'?{phase:'idle',asr_connected:false,backend_busy:false}
  :{transport:'reachable'};
 return {ok:true,status:200,json:async()=>value};
};
window.addEventListener('load',()=>setTimeout(async()=>{
 const pause=()=>new Promise(r=>setTimeout(r,100));
 const input=document.getElementById('messageInput');
 input.value='网络失败也要保留的问题';input.dispatchEvent(new Event('input'));
 document.getElementById('chatForm').requestSubmit();await pause();
 document.body.dataset.chatUser=String(document.querySelector('.message.user .message-body')?.textContent==='网络失败也要保留的问题');
 document.getElementById('newConversation').click();await pause();
 const notices=[...document.querySelectorAll('.message.notice .message-body')].map(e=>e.textContent);
 document.body.dataset.localized=String(notices.length===2 && notices.every(t=>t.includes('新助手')&&t.includes('暂时无法连接')&&!t.includes('Failed to fetch')));
 document.body.dataset.clearUser=String(document.querySelector('.message.user .message-body')?.textContent==='网络失败也要保留的问题');
 document.body.dataset.noPending=String(!document.querySelector('.message.pending'));
 window.abortChat=true;input.value='超时的提问';input.dispatchEvent(new Event('input'));
 document.getElementById('chatForm').requestSubmit();await pause();
 document.body.dataset.abortPreserved=String([...document.querySelectorAll('.message.notice .message-body')]
   .some(e=>e.textContent.includes('本次请求可能仍在处理')));
},250));
</script>"""
        html = (UI / "index.html").read_text().replace(
            '<script src="/app.js" defer></script>', fixture + '<script src="/app.js" defer></script>')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/", "/app.js", "/media.js", "/style.css"):
                    self.send_error(404)
                    return
                body = html.encode() if self.path == "/" else (UI / self.path[1:]).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8" if self.path == "/" else "text/javascript" if self.path.endswith(".js") else "text/css")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([shutil.which("chromium"), "--headless", "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage", "--disable-background-networking", "--virtual-time-budget=2000",
                    "--dump-dom", f"--user-data-dir={directory}", f"http://127.0.0.1:{server.server_port}/"],
                    capture_output=True, text=True, timeout=30)
            finally:
                server.shutdown()
                thread.join(2)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        body = re.search(r"<body\b([^>]*)>", result.stdout)
        self.assertIsNotNone(body)
        for attribute in ("chat-user", "clear-user", "no-pending", "abort-preserved", "localized"):
            self.assertIn(f'data-{attribute}="true"', body.group(1))
