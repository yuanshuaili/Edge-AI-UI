"""Native cross-origin browser attacks and EventSource restart, no models."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest

UI = Path(__file__).resolve().parents[1] / "ui"


def chromium(url, profile):
    result = subprocess.run([shutil.which("chromium"), "--headless", "--no-sandbox",
        "--disable-gpu", "--disable-dev-shm-usage", "--disable-background-networking",
        "--virtual-time-budget=3500", f"--user-data-dir={profile}", "--dump-dom", url],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr[-800:])
    return result.stdout


@unittest.skipUnless(shutil.which("chromium"), "Chromium is not installed")
class BrowserSecurityTests(unittest.TestCase):
    def test_native_no_cors_and_iframe_cannot_control_or_saturate_ui(self):
        from ui_backend.server import create_server
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = {"id": "mock", "name": "示例", "device": "设备", "mode": "Local",
                       "adapter": "mock", "endpoint": "mock://local",
                       "capabilities": {"text": True, "voice": False, "image": False, "video": False, "tts": False},
                       "available_inputs": {"text": True, "voice": False, "image": False, "video": False}}
            (root / "backends.json").write_text(json.dumps({"default_backend_id": "mock", "backends": [profile]}))
            (root / "assistant.json").write_text(json.dumps({"id": "a", "name": "助手", "subtitle": "本地",
                                                             "welcome": "欢迎", "system_prompt": "中文"}))
            target = create_server(root / "backends.json", port=0, assistant_path=root / "assistant.json")
            records = []
            original = target.RequestHandlerClass
            class ObservedHandler(original):
                def log_message(self, fmt, *args):
                    if len(args) >= 2:
                        records.append((self.path, str(args[1]), self.headers.get("Sec-Fetch-Site")))
            target.RequestHandlerClass = ObservedHandler
            target_thread = threading.Thread(target=target.serve_forever, daemon=True)
            target_thread.start()
            url = f"http://127.0.0.1:{target.server_port}"
            page = ("<body><iframe src=" + json.dumps(url + "/") + "></iframe><script>"
                    "(async()=>{for(let n=0;n<5;n++){const c=new AbortController();"
                    "setTimeout(()=>c.abort(),400);try{await fetch(" + json.dumps(url + "/api/voice/events") +
                    ",{mode:'no-cors',signal:c.signal});}catch{}}"
                    "document.body.dataset.done='true';})();</script></body>").encode()
            class Attacker(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                def log_message(self, *_args):
                    pass
            with ThreadingHTTPServer(("127.0.0.1", 0), Attacker) as attacker:
                thread = threading.Thread(target=attacker.serve_forever, daemon=True)
                thread.start()
                try:
                    dom = chromium(f"http://127.0.0.1:{attacker.server_port}/", root / "browser")
                finally:
                    attacker.shutdown()
                    thread.join(2)
                    target.shutdown()
                    target.server_close()
                    target_thread.join(2)
            self.assertIn('data-done="true"', dom)
            event_requests = [row for row in records if row[0] == "/api/voice/events"]
            self.assertEqual(len(event_requests), 5, records)
            self.assertTrue(all(row[1] == "403" for row in event_requests), records)
            self.assertFalse(any(row[0] in ("/api/assistant", "/api/backends") for row in records),
                             "Cross-origin iframe initialized the real UI")

    def test_event_source_reconnects_when_server_token_rotates(self):
        fixture = """<script>
window.streamURLs=[];window.closedStreams=0;window.currentToken='first';
window.EventSource=class {constructor(url){streamURLs.push(url);}addEventListener(){}close(){closedStreams++;}};
window.fetch=async(url)=>{
 const value=url==='/api/assistant'?{id:'a',name:'助手',subtitle:'本地',welcome:'欢迎'}
 :url==='/api/backends'?{default_backend_id:'mock',event_stream_token:'first',backends:[{id:'mock',name:'示例',device:'设备',
 capabilities:{text:true,voice:false,image:false,video:false},available_inputs:{text:true,voice:false,image:false,video:false}}]}
 :url==='/api/voice/state'?{phase:'idle',asr_connected:false,backend_busy:false,event_stream_token:currentToken}
 :{transport:'reachable'};
 return {ok:true,status:200,json:async()=>value};
};
window.addEventListener('load',()=>setTimeout(async()=>{
 window.currentToken='second';await refreshVoiceState();
 document.body.dataset.renewed=String(state.eventStreamToken==='second' && streamURLs.at(-1)==='/api/voice/events?token=second' && closedStreams>=1);
},300));
</script>"""
        html = (UI / "index.html").read_text().replace('<script src="/app.js" defer></script>',
                                                     fixture + '<script src="/app.js" defer></script>')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/", "/app.js", "/media.js", "/style.css"):
                    self.send_error(404)
                    return
                body = html.encode() if self.path == "/" else (UI / self.path[1:]).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if self.path == "/" else
                                 "text/css" if self.path.endswith(".css") else "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                dom = chromium(f"http://127.0.0.1:{server.server_port}/", directory)
            finally:
                server.shutdown()
                thread.join(2)
        body = re.search(r"<body\b([^>]*)>", dom)
        self.assertIsNotNone(body)
        self.assertIn('data-renewed="true"', body.group(1))
