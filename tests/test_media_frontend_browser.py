"""Native DOM/File integration with a bounded fake transport, no model/browser mic."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest

UI=Path(__file__).resolve().parents[1]/"ui"

@unittest.skipUnless(shutil.which("chromium"),"Chromium not installed")
class MediaFrontendTests(unittest.TestCase):
    def test_upload_preview_attachment_only_error_and_cancellation(self):
        fixture="""<script>
window.EventSource=class {addEventListener(){} close(){}};
window.createdURLs=[]; window.revokedURLs=[]; window.deleted=[];
URL.createObjectURL=()=>{const u='blob:fixture-'+createdURLs.length;createdURLs.push(u);return u;};
URL.revokeObjectURL=(u)=>revokedURLs.push(u);
window.fetch=async(url,options={})=>{
 let value,ok=true;
 if(url==='/api/assistant')value={id:'assistant',name:'助手',subtitle:'本地',welcome:'欢迎'};
 else if(url==='/api/backends')value={default_backend_id:'media',api_features:{uploads:true},attachment_limits:{image_bytes:10485760,video_bytes:52428800},backends:[
 {id:'media',name:'媒体模拟',device:'设备',capabilities:{text:true,voice:false,image:true,video:true},available_inputs:{text:true,voice:false,image:true,video:true}},
 {id:'text',name:'文本模拟',device:'设备',capabilities:{text:true,voice:false,image:false,video:false},available_inputs:{text:true,voice:false,image:false,video:false}}]};
 else if(url==='/api/voice/state')value={selected_backend_id:'media',phase:'idle',asr_connected:false,request_id:null,backend_busy:false};
 else if(url==='/api/voice/selection')value={selected_backend_id:JSON.parse(options.body).backend_id,phase:'idle',asr_connected:false,request_id:null,backend_busy:false};
 else if(url==='/api/chat'){window.lastChat=JSON.parse(options.body);ok=!window.failChat;value=ok?{text:'模拟回复',latency_ms:1}:{error:{message:'请求失败'}};}
 else if(options.method==='DELETE'){window.deleted.push(url);value={ok:true};}
 else value={transport:'reachable'};
 return {ok,status:ok?200:503,json:async()=>value};
};
window.XMLHttpRequest=class {
 constructor(){this.upload={};}
 open(method,url){this.url=url;}
 setRequestHeader(){}
 send(file){this.upload.onprogress?.({lengthComputable:true,loaded:file.size,total:file.size});
   setTimeout(()=>{this.status=201;this.responseText=JSON.stringify({attachment_id:'id-'+createdURLs.length,kind:file.type.startsWith('image/')?'image':'video',size:file.size,media_type:file.type});this.onload?.();},window.delayUpload?250:5);}
 abort(){this.onabort?.();}
};
window.addEventListener('load',()=>setTimeout(async()=>{
 try {
  const file=new File(['fixture'],'sample.png',{type:'image/png'});
  await state.media.upload(file,'image','media');
  document.body.dataset.attachmentOnlySendEnabled=String(!document.getElementById('sendButton').disabled);
  document.body.dataset.previewVisible=String(!document.getElementById('attachmentPreview').hidden);
  window.failChat=true;document.getElementById('chatForm').requestSubmit();
  await new Promise(r=>setTimeout(r,50));
  document.body.dataset.userPreservedAfterError=String(!!document.querySelector('.message.user'));
  document.body.dataset.attachmentRouted=String(!!window.lastChat.attachment_id && window.lastChat.backend_id==='media');
  document.body.dataset.abandonedAttachmentDiscarded=String(window.deleted.includes('/api/attachments/'+encodeURIComponent(window.lastChat.attachment_id)));
  window.delayUpload=true;
  const pending=state.media.upload(file,'image','media').catch(()=>{});
  state.media.clear();await pending;await new Promise(r=>setTimeout(r,300));
  document.body.dataset.lateUploadDiscarded=String(state.media.pending===null);
  document.body.dataset.allPreviewUrlsRevoked=String(createdURLs.length===revokedURLs.length);
  const selector=document.getElementById('backendSelect');selector.value='text';selector.dispatchEvent(new Event('change'));
  await new Promise(r=>setTimeout(r,50));
  document.body.dataset.unsupportedDisabled=String(document.getElementById('imageButton').disabled&&document.getElementById('videoButton').disabled);
  const voice=document.getElementById('voiceButton').getBoundingClientRect();const send=document.getElementById('sendButton').getBoundingClientRect();
  document.body.dataset.buttonsSeparated=String(voice.right<=send.left);
 } catch(e){document.body.dataset.fixtureError=e.message;}
},150));
</script>"""
        html=(UI/"index.html").read_text().replace('<script src="/app.js" defer></script>',fixture+'<script src="/app.js" defer></script>')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path=="/": body=html.encode();mime="text/html"
                elif self.path in ("/app.js","/media.js","/style.css"):
                    path=UI/self.path[1:]
                    if not path.exists(): self.send_error(404);return
                    body=path.read_bytes();mime="text/css" if path.suffix==".css" else "text/javascript"
                else: self.send_error(404);return
                self.send_response(200);self.send_header("Content-Type",mime);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        for width in (360,1366):
            with self.subTest(width=width),ThreadingHTTPServer(("127.0.0.1",0),Handler) as server,tempfile.TemporaryDirectory() as profile:
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    result=subprocess.run([shutil.which("chromium"),"--headless","--no-sandbox","--disable-gpu","--disable-dev-shm-usage","--disable-background-networking",
                        "--virtual-time-budget=2500",f"--window-size={width},900",f"--user-data-dir={profile}","--dump-dom",f"http://127.0.0.1:{server.server_port}/"],capture_output=True,text=True,timeout=30)
                finally: server.shutdown();thread.join(2)
            self.assertEqual(result.returncode,0,result.stderr[-500:]);body=re.search(r"<body\b([^>]*)>",result.stdout)
            self.assertIsNotNone(body)
            for attribute in ("attachment-only-send-enabled","preview-visible","user-preserved-after-error","attachment-routed","abandoned-attachment-discarded","late-upload-discarded","all-preview-urls-revoked","unsupported-disabled","buttons-separated"):
                self.assertIn(f'data-{attribute}="true"',body.group(1))
