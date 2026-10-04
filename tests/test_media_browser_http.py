"""Native Chromium XHR/fetch/SSE against the real UI backend, Mock only."""
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest


@unittest.skipUnless(shutil.which("chromium"), "Chromium not installed")
class MediaBrowserHttpTests(unittest.TestCase):
    def test_native_upload_and_attachment_only_reply(self):
        from ui_backend.server import create_server
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = {"id": "media", "name": "模拟媒体", "device": "示例设备", "mode": "Local",
                       "adapter": "mock-media", "endpoint": "mock://local",
                       "capabilities": {"text": True, "voice": False, "image": True, "video": True, "tts": False},
                       "available_inputs": {"text": True, "voice": False, "image": True, "video": True}}
            (root / "backends.json").write_text(json.dumps({"default_backend_id": "media", "backends": [profile]}))
            (root / "assistant.json").write_text(json.dumps({"id": "sample", "name": "助手", "subtitle": "本地", "welcome": "欢迎", "system_prompt": "中文"}))
            server = create_server(root / "backends.json", port=0, assistant_path=root / "assistant.json")
            original = server.RequestHandlerClass
            fixture = """<script>
// Chromium virtual time pauses while a long-lived response is pending. Keep
// the real SSE handshake, then close it; continuous SSE is tested via sockets.
const NativeEventSource=window.EventSource;
window.EventSource=class extends NativeEventSource {
 constructor(url){super(url);this.addEventListener('open',()=>this.close(),{once:true});}
};
window.addEventListener('load', async () => {
 try {
  const pause=()=>new Promise(r=>setTimeout(r,80));
  for(let n=0;n<50 && (!state.media || !modelReady());n++) await pause();
  const png=atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jrNcAAAAASUVORK5CYII=');
  const file=new File([Uint8Array.from(png,c=>c.charCodeAt(0))],'sample.png',{type:'image/png'});
  await state.media.upload(file,'image','media');
  document.body.dataset.nativeUpload=String(!!state.media.pending);
  document.getElementById('chatForm').requestSubmit();
  for(let n=0;n<50 && !document.querySelector('.message.assistant:not(.pending) .message-body');n++) await pause();
  const answer=document.querySelector('.message.assistant:not(.pending) .message-body')?.textContent || '';
  document.body.dataset.nativeReply=String(answer.includes('[media]') && answer.includes('image') && answer.includes('模拟，未分析媒体内容'));
  document.body.dataset.nativeUser=String(!!document.querySelector('.message.user'));
  document.body.dataset.nativeReleased=String(state.media.pending===null);
 } catch(error) {document.body.dataset.nativeError=error.message;}
 finally {state.voiceEventSource?.close();}
});
</script>"""
            html = (Path(__file__).resolve().parents[1] / "ui/index.html").read_text().replace(
                '<script src="/app.js" defer></script>', fixture + '<script src="/app.js" defer></script>')
            class FixtureHandler(original):
                def do_GET(self):
                    if self.path != "/": return super().do_GET()
                    if not self._require_valid_host(): return
                    body = html.encode()
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            server.RequestHandlerClass = FixtureHandler
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                result = subprocess.run([shutil.which("chromium"), "--headless", "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage", "--disable-background-networking", "--virtual-time-budget=8000",
                    f"--user-data-dir={root / 'browser'}", "--dump-dom", f"http://127.0.0.1:{server.server_port}/"],
                    capture_output=True, text=True, timeout=35)
            finally:
                server.shutdown(); server.server_close(); thread.join(2)
            self.assertEqual(result.returncode, 0, result.stderr[-500:])
            body = re.search(r"<body\b([^>]*)>", result.stdout)
            self.assertIsNotNone(body)
            for attribute in ("native-upload", "native-reply", "native-user", "native-released"):
                self.assertIn(f'data-{attribute}="true"', body.group(1))
            self.assertEqual(server.attachment_store.reserved_bytes, 0)
