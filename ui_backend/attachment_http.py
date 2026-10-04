"""Fixed-length binary uploads with early framing checks and total deadlines."""
import socket
import time
from .attachments import AttachmentError

class AttachmentHttp:
    def __init__(self,store,dispatcher,selection_provider,can_upload):
        self.store,self.dispatcher,self.selection_provider,self.can_upload=store,dispatcher,selection_provider,can_upload

    def _preflight(self,handler,query):
        if set(query)!={"kind","backend_id"} or any(len(v)!=1 for v in query.values()):
            raise AttachmentError("invalid_request","附件请求格式无效。")
        lengths=handler.headers.get_all("Content-Length",[])
        if len(lengths)!=1 or handler.headers.get_all("Transfer-Encoding") or len(lengths[0])>20 or not lengths[0].isascii() or not lengths[0].isdecimal():
            raise AttachmentError("invalid_length","附件需要唯一的固定长度。")
        kind,backend_id=query["kind"][0],query["backend_id"][0]
        if not self.can_upload(backend_id,kind):
            raise AttachmentError("media_unavailable","当前模型暂不可接收此类附件。",409)
        return self.store.reserve(self.selection_provider(),kind,int(lengths[0]),handler.headers.get_content_type())

    def upload(self,handler,query):
        reservation=None
        handler.close_connection=True
        try:
            reservation=getattr(handler,"_upload_reservation",None) or self._preflight(handler,query)
            stamp=self.selection_provider()
            deadline=time.monotonic()+self.store.limits.upload_seconds
            def read_chunk(size,remaining):
                handler.connection.settimeout(min(handler.server.client_timeout,remaining))
                return handler.rfile.read1(size)
            attachment=self.store.receive(reservation,read_chunk,deadline)
            if self.selection_provider()!=stamp or not self.can_upload(stamp.backend_id,attachment.kind):
                self.store.discard(attachment.id)
                raise AttachmentError("stale_attachment","模型或对话已变化，请重新选择附件。",409)
            handler._json(201,self.store.metadata(attachment.id))
        except AttachmentError as exc: handler._error(exc.http_status,exc.code,str(exc))
        except (TimeoutError,socket.timeout): handler._error(408,"upload_timeout","附件上传超时，请重试。")
        except OSError: handler._error(400,"upload_incomplete","附件上传未完成，请重试。")
        finally:
            if reservation is not None:
                # receive already cleans on read failure; completed files remain idle.
                self.store.abort_upload(reservation)

    def expect(self,handler,query):
        try:
            handler._upload_reservation=self._preflight(handler,query)
        except AttachmentError as exc:
            handler.close_connection=True; handler._error(exc.http_status,exc.code,str(exc)); return False
        try:
            handler.send_response_only(100); handler.end_headers()
        except BaseException:
            self.store.abort_upload(handler._upload_reservation)
            del handler._upload_reservation
            raise
        return True

    def delete(self,handler,identifier):
        try:
            removed=self.store.discard(identifier)
            if not removed: raise AttachmentError("attachment_busy","当前附件仍在处理中。",409)
            handler._json(200,{"ok":True})
        except AttachmentError as exc: handler._error(exc.http_status,exc.code,str(exc))
