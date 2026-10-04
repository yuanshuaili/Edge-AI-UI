"""Binary upload protocol checks use loopback and tiny format fixtures only."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from test_attachments import PNG, FIXTURES

class AttachmentHttpTests(unittest.TestCase):
    def setUp(self):
        from ui_backend.server import create_server
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        profile={"id":"media","name":"模拟媒体","device":"示例设备","mode":"Local","adapter":"mock-media","endpoint":"mock://local",
                 "capabilities":{"text":True,"voice":False,"image":True,"video":True,"tts":False},
                 "available_inputs":{"text":True,"voice":False,"image":True,"video":True}}
        (root/"backends.json").write_text(json.dumps({"default_backend_id":"media","backends":[profile,dict(profile,id="other")]}))
        (root/"assistant.json").write_text(json.dumps({"id":"sample","name":"助手","subtitle":"本地","welcome":"欢迎","system_prompt":"中文"}))
        self.server=create_server(root/"backends.json",port=0,assistant_path=root/"assistant.json")
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        try:
            connection.request(method,path,body,headers or {})
            response=connection.getresponse(); return response.status,json.loads(response.read())
        finally: connection.close()

    def post(self,path,data,headers=None):
        return self.request("POST",path,json.dumps(data),{"Content-Type":"application/json",**(headers or {})})

    def upload(self,kind="image",mime="image/png",body=PNG):
        return self.request("POST","/api/attachments?kind="+kind+"&backend_id=media",body,{"Content-Type":mime})

    def test_image_video_attachment_only_and_legacy_text(self):
        for kind,mime,body in FIXTURES:
            status,metadata=self.upload(kind,mime,body); self.assertEqual(status,201,metadata)
            self.assertNotIn("local_path",metadata); self.assertNotIn("filename",metadata)
            status,reply=self.post("/api/chat",{"backend_id":"media","text":"","attachment_id":metadata["attachment_id"]})
            self.assertEqual(status,200,reply); self.assertIn(kind,reply["text"])
            self.assertIn("模拟，未分析媒体内容",reply["text"])
            self.assertEqual(self.post("/api/chat",{"backend_id":"media","text":"","attachment_id":metadata["attachment_id"]})[0],409)
        status,reply=self.post("/api/chat",{"backend_id":"media","text":"中文"})
        self.assertEqual(status,200); self.assertIn("中文",reply["text"])

    def test_wrong_origin_bad_fields_reset_and_aba_stamp(self):
        self.assertEqual(self.post("/api/model/activate",{"backend_id":"other"},{"Origin":"http://evil.invalid"})[0],403)
        for extra in ({"path":"/tmp/x"},{"url":"http://evil"},{"command":"true"},{"session_id":"other"}):
            self.assertEqual(self.post("/api/chat",{"backend_id":"media","text":"a",**extra})[0],400)
        _,metadata=self.upload(); identifier=metadata["attachment_id"]
        self.post("/api/model/activate",{"backend_id":"other"}); self.server.model_manager.wait(2)
        self.post("/api/model/activate",{"backend_id":"media"}); self.server.model_manager.wait(2)
        self.assertEqual(self.post("/api/chat",{"backend_id":"media","text":"","attachment_id":identifier})[0],409)
        _,metadata=self.upload(); self.assertEqual(self.post("/api/session/clear",{"backend_id":"media"})[0],200)
        self.assertEqual(self.post("/api/chat",{"backend_id":"media","text":"","attachment_id":metadata["attachment_id"]})[0],409)

    def test_delete_unknown_id_and_mime_mismatch(self):
        self.assertEqual(self.upload(body=b"<svg/>")[0],415)
        _,metadata=self.upload()
        self.assertEqual(self.request("DELETE","/api/attachments/"+metadata["attachment_id"])[0],200)
        self.assertEqual(self.request("DELETE","/api/attachments/../../x")[0],400)
        self.assertEqual(self.server.attachment_store.reserved_bytes,0)

    def test_invalid_framing_rejected_before_body(self):
        port=self.server.server_port
        for headers in ("", "Content-Length: -1\r\n", "Content-Length: 999999999\r\n",
                        "Content-Length: 5\r\nContent-Length: 5\r\n",
                        "Transfer-Encoding: chunked\r\n", "Content-Length: 5\r\nTransfer-Encoding: chunked\r\n"):
            with self.subTest(headers=headers), socket.create_connection(("127.0.0.1",port),timeout=2) as client:
                client.sendall((f"POST /api/attachments?kind=image&backend_id=media HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: image/png\r\n{headers}\r\n").encode())
                status=client.recv(1024).split(b"\r\n",1)[0]
                self.assertIn(status.split()[1],(b"400",b"413"),status)
        self.assertEqual(self.server.attachment_store.reserved_bytes,0)

    def test_expect_continue_validates_before_acknowledgement(self):
        port=self.server.server_port
        with socket.create_connection(("127.0.0.1",port),timeout=2) as client:
            client.sendall((f"POST /api/attachments?kind=image&backend_id=media HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: image/png\r\nContent-Length: {len(PNG)}\r\nExpect: 100-continue\r\n\r\n").encode())
            first=client.recv(1024)
            self.assertIn(b"100 Continue",first)
            client.sendall(PNG)
            final=client.recv(2048); self.assertIn(b"201 Created",final)

    def test_short_body_and_total_deadline_leave_no_partial(self):
        import time
        from dataclasses import replace
        port=self.server.server_port
        self.server.attachment_store.limits=replace(self.server.attachment_store.limits,upload_seconds=.12)
        for partial in (b"",PNG[:4]):
            with socket.create_connection(("127.0.0.1",port),timeout=2) as client:
                client.sendall((f"POST /api/attachments?kind=image&backend_id=media HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: image/png\r\nContent-Length: {len(PNG)}\r\n\r\n").encode()+partial)
                if not partial: client.shutdown(socket.SHUT_WR)
                response=client.recv(2048)
                self.assertIn(response.split()[1],(b"400",b"408"),response)
            deadline=time.monotonic()+1
            while self.server.attachment_store.reserved_bytes and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(self.server.attachment_store.reserved_bytes,0)

    def test_expect_rejects_bad_framing_origin_and_method_without_reservation(self):
        port=self.server.server_port
        for method,headers,want in (("POST","Content-Length: 999999999\r\n",b"413"),
                                   ("POST","Content-Length: 8\r\nOrigin: http://evil.invalid\r\n",b"403"),
                                   ("GET","Content-Length: 8\r\n",b"417")):
            with self.subTest(method=method,headers=headers),socket.create_connection(("127.0.0.1",port),timeout=2) as client:
                client.sendall((f"{method} /api/attachments?kind=image&backend_id=media HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: image/png\r\nExpect: 100-continue\r\n{headers}\r\n").encode())
                first=client.recv(2048)
                self.assertEqual(first.split()[1],want,first)
            self.assertEqual(self.server.attachment_store.reserved_bytes,0)

    def test_rejected_json_does_not_parse_unread_body_as_another_request(self):
        port=self.server.server_port
        for path,headers in (("/api/chat","Content-Type: text/plain\r\n"),
                             ("/api/chat","Content-Type: application/json\r\nContent-Length: 8\r\n"),
                             ("/missing","Content-Type: application/json\r\n")):
            with self.subTest(path=path,headers=headers),socket.create_connection(("127.0.0.1",port),timeout=2) as client:
                smuggled=f"GET /api/assistant HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()
                client.sendall((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {len(smuggled)}\r\n{headers}\r\n").encode()+smuggled)
                result=b""
                while True:
                    chunk=client.recv(4096)
                    if not chunk: break
                    result+=chunk
                self.assertNotIn(b"200 OK",result)

    def test_normal_close_waits_for_media_lease_before_cleanup(self):
        _,metadata=self.upload()
        adapter=self.server.model_manager.adapters["media"]
        entered=threading.Event(); release=threading.Event(); closed=threading.Event()
        seen=[]; replies=[]
        original=adapter.image
        def slow(payload):
            seen.append(payload.attachment.local_path); entered.set()
            if not release.wait(3): raise RuntimeError("test release timed out")
            self.assertTrue(seen[0].exists())
            return original(payload)
        adapter.image=slow
        sender=threading.Thread(target=lambda:replies.append(self.post("/api/chat",{"backend_id":"media","text":"","attachment_id":metadata["attachment_id"]})))
        sender.start(); self.assertTrue(entered.wait(2))
        def close():
            self.server.shutdown(); self.server.server_close(); closed.set()
        closer=threading.Thread(target=close); closer.start()
        try:
            self.assertFalse(closed.wait(.15)); self.assertTrue(seen[0].exists())
        finally:
            release.set(); sender.join(3); closer.join(3)
        self.assertTrue(closed.is_set()); self.assertEqual(replies[0][0],200)
        self.assertFalse(self.server.attachment_store.directory.exists())

    def test_close_with_open_sse_runs_model_cleanup(self):
        from unittest.mock import patch
        waiter=self.server.voice.wait_for_events
        self.server.voice.wait_for_events=lambda last,timeout=5:waiter(last,.05)
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=2)
        connection.request("GET","/api/voice/events"); response=connection.getresponse()
        self.assertEqual(response.status,200); self.assertTrue(response.readline())
        closed=threading.Event()
        with patch.object(self.server.model_manager,"close") as cleanup:
            def close():
                self.server.shutdown(); self.server.server_close(); closed.set()
            closer=threading.Thread(target=close); closer.start()
            try:
                self.assertTrue(closed.wait(1),"SSE blocked normal shutdown")
                cleanup.assert_called_once()
            finally:
                response.close(); connection.close(); closer.join(2)

    def test_reset_cannot_overlap_admitted_media_lease(self):
        dispatcher=self.server.voice.dispatcher
        entered=threading.Event(); release=threading.Event(); replies=[]
        _,metadata=self.upload()
        original=dispatcher.dispatch_media_chat
        def paused(*args,**kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError("test release timed out")
            return original(*args,**kwargs)
        dispatcher.dispatch_media_chat=paused
        sender=threading.Thread(target=lambda:replies.append(self.post("/api/chat",{"backend_id":"media","text":"","attachment_id":metadata["attachment_id"]})))
        sender.start(); self.assertTrue(entered.wait(2))
        try:
            self.assertEqual(self.post("/api/session/clear",{"backend_id":"media"})[0],409)
        finally:
            release.set(); sender.join(3); dispatcher.dispatch_media_chat=original
        self.assertEqual(replies[0][0],200)

    def test_media_consumption_is_rejected_during_voice_capture(self):
        profile=self.server.voice.config.backends["media"]
        profile.capabilities["voice"]=True; profile.available_inputs["voice"]=True
        self.server.voice.attach_sender(lambda message:None); self.server.voice.set_asr_connected(True)
        _,metadata=self.upload(); self.server.voice.start_listening()
        try:
            self.assertEqual(self.post("/api/chat",{"backend_id":"media","text":"","attachment_id":metadata["attachment_id"]})[0],409)
            self.assertEqual(self.server.voice.phase,"listening")
        finally: self.server.voice.cancel_listening()

    def test_failed_continue_ack_releases_reservation(self):
        from email.message import Message
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ui_backend.attachment_http import AttachmentHttp
        from ui_backend.media import SelectionStamp
        stamp=SelectionStamp("media","test",1)
        headers=Message(); headers["Content-Length"]=str(len(PNG)); headers["Content-Type"]="image/png"
        handler=SimpleNamespace(headers=headers,send_response_only=Mock(),end_headers=Mock(side_effect=BrokenPipeError()))
        protocol=AttachmentHttp(self.server.attachment_store,None,lambda:stamp,lambda *_:True)
        with self.assertRaises(BrokenPipeError): protocol.expect(handler,{"kind":["image"],"backend_id":["media"]})
        self.assertEqual(self.server.attachment_store.reserved_bytes,0)
        self.assertEqual(self.upload()[0],201)
