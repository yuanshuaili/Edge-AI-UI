import threading
import unittest
from dataclasses import replace
from test_chat_dispatch import mock_profile
from test_attachments import PNG

class MediaDispatchTests(unittest.TestCase):
    def setup_media(self, adapter_class=None):
        from ui_backend.attachments import AttachmentStore, AttachmentLimits
        from ui_backend.media import SelectionStamp
        from ui_backend.media_adapters import MockMediaAdapter
        from ui_backend.chat import ChatDispatcher
        from ui_backend.config import AppConfig
        profile = replace(mock_profile("media"), adapter="mock-media",
            capabilities={"text":True,"voice":False,"image":True,"video":True,"tts":False},
            available_inputs={"text":True,"voice":False,"image":True,"video":True})
        adapter = (adapter_class or MockMediaAdapter)(profile)
        dispatcher = ChatDispatcher(AppConfig("","media",{"media":profile}),{"media":adapter})
        stamp = SelectionStamp("media","epoch",1)
        dispatcher.set_selection_provider(lambda:stamp)
        store = AttachmentStore(AttachmentLimits()); self.addCleanup(store.close)
        reservation=store.reserve(stamp,"image",len(PNG),"image/png")
        attachment=store.receive(reservation,lambda n,t:PNG,store.clock()+30)
        return dispatcher, store, stamp, attachment

    def test_image_calls_media_operation_not_text(self):
        dispatcher,store,stamp,attachment=self.setup_media()
        with store.consume(attachment.id,stamp) as leased:
            result=dispatcher.dispatch_media_chat("media","",leased,stamp)
        self.assertIn("[media]",result.text)
        self.assertIn("image",result.text)
        self.assertIn("模拟，未分析媒体内容",result.text)
        self.assertFalse(attachment.local_path.exists())
        self.assertIn("已收到：你好",dispatcher.dispatch_text_chat("media","你好").text)

    def test_revision_change_and_transition_reject_dispatch(self):
        from ui_backend.attachments import AttachmentError
        from ui_backend.chat import BackendBusy
        from ui_backend.media import SelectionStamp
        dispatcher,store,stamp,attachment=self.setup_media()
        dispatcher.set_selection_provider(lambda:SelectionStamp("media","epoch",3))
        with self.assertRaises(AttachmentError): dispatcher.dispatch_media_chat("media","",attachment,stamp)
        dispatcher.gate.begin_transition()
        try:
            with self.assertRaises(BackendBusy): dispatcher.dispatch_media_chat("media","",attachment,stamp)
        finally: dispatcher.gate.end_transition()

    def test_busy_lease_survives_until_adapter_finishes(self):
        from ui_backend.media_adapters import MockMediaAdapter
        from ui_backend.chat import BackendBusy
        started=threading.Event(); release=threading.Event()
        class Waiting(MockMediaAdapter):
            def image(self,payload):
                started.set(); release.wait(2)
                return super().image(payload)
        dispatcher,store,stamp,attachment=self.setup_media(Waiting)
        results=[]
        def run():
            with store.consume(attachment.id,stamp) as leased:
                results.append(dispatcher.dispatch_media_chat("media","",leased,stamp))
        worker=threading.Thread(target=run); worker.start()
        try:
            self.assertTrue(started.wait(1)); self.assertTrue(dispatcher.is_busy("media"))
            for operation in (lambda:dispatcher.clear_session("media"), dispatcher.gate.begin_transition):
                with self.assertRaises(BackendBusy): operation()
            store.expire(); self.assertFalse(store.discard(attachment.id))
            self.assertTrue(attachment.local_path.exists())
        finally: release.set(); worker.join(3)
        self.assertFalse(dispatcher.is_busy("media")); self.assertFalse(attachment.local_path.exists())
