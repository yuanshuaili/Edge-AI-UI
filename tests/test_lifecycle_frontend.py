"""Browser lifecycle gating and selector behavior without loading models."""
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
class LifecycleBrowserTests(unittest.TestCase):
    def test_starting_disables_inputs_and_ready_switch_selects_mock(self):
        fixture = """<script>
window.EventSource = class {constructor(){window.events=this;} addEventListener(name,fn){if(name==='voice')this.fn=fn;}
  emit(value){this.fn({data:JSON.stringify(value)});}};
let model={state:'offline',selected_backend_id:'q',target_backend_id:null,owned:false,transitioning:false,revision:0};
window.fetch=async(url,options={})=>{
  let value;
  if(url==='/api/assistant')value={id:'a',name:'助手',subtitle:'本地',welcome:'欢迎'};
  else if(url==='/api/backends')value={api_features:{model_lifecycle:true},default_backend_id:'q',backends:[
    {id:'q',name:'测试模型',device:'设备',managed:true,capabilities:{text:true,voice:true,image:false,video:false},available_inputs:{text:true,voice:true,image:false,video:false}},
    {id:'mock',name:'模拟模型',device:'设备',managed:false,capabilities:{text:true,voice:false,image:false,video:false},available_inputs:{text:true,voice:false,image:false,video:false}}]};
  else if(url==='/api/model/state')value=model;
  else if(url==='/api/model/activate'){model={...model,state:'starting',target_backend_id:JSON.parse(options.body).backend_id,transitioning:true,revision:1};value=model;}
  else if(url==='/api/voice/state')value={phase:'idle',asr_connected:true,backend_busy:false};
  else value={transport:'offline'};
  return {ok:true,status:200,json:async()=>value};
};
window.addEventListener('load',()=>setTimeout(async()=>{
  const pause=()=>new Promise(r=>setTimeout(r,100));
  document.body.dataset.offlineInput=String(document.getElementById('messageInput').disabled);
  document.getElementById('modelStart').click();await pause();
  document.body.dataset.startingSelector=String(document.getElementById('backendSelect').disabled);
  document.body.dataset.startingMic=String(document.getElementById('voiceButton').disabled);
  document.body.dataset.startingClear=String(document.getElementById('newConversation').disabled);
  model={...model,state:'ready',selected_backend_id:'q',target_backend_id:null,owned:true,transitioning:false,revision:2};
  window.events.emit({type:'model_state',model_state:model});await pause();
  document.body.dataset.readyInput=String(!document.getElementById('messageInput').disabled);
  const selector=document.getElementById('backendSelect');selector.value='mock';selector.dispatchEvent(new Event('change'));await pause();
  model={...model,state:'ready',selected_backend_id:'mock',target_backend_id:null,owned:false,transitioning:false,revision:3};
  window.events.emit({type:'model_state',model_state:model});await pause();
  document.body.dataset.finalSelected=selector.value;
  document.body.dataset.mockMic=String(document.getElementById('voiceButton').disabled);
},250));
</script>"""
        html = (UI / "index.html").read_text().replace('<script src="/app.js" defer></script>', fixture + '<script src="/app.js" defer></script>')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/", "/app.js", "/media.js", "/style.css"):
                    self.send_error(404)
                    return
                body = html.encode() if self.path == "/" else (UI / self.path[1:]).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if self.path == "/" else "text/javascript" if self.path.endswith("js") else "text/css")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, TemporaryDirectory() as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([shutil.which("chromium"), "--headless", "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage", "--disable-background-networking", "--virtual-time-budget=2500",
                    "--dump-dom", f"--user-data-dir={directory}", f"http://127.0.0.1:{server.server_port}/"],
                    capture_output=True, text=True, timeout=30)
            finally:
                server.shutdown()
                thread.join(2)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        body = re.search(r"<body\b([^>]*)>", result.stdout)
        self.assertIsNotNone(body)
        for expected in ('data-offline-input="true"', 'data-starting-selector="true"', 'data-starting-mic="true"',
                         'data-starting-clear="true"', 'data-ready-input="true"', 'data-final-selected="mock"', 'data-mock-mic="true"'):
            self.assertIn(expected, body.group(1))
