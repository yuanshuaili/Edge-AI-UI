"""Bounded private temporary files. Header checks are NOT safe/full decoding."""
from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import uuid
from .media import Attachment, SelectionStamp

MIME_TYPES = {"image": ("image/jpeg", "image/png", "image/webp"),
              "video": ("video/mp4", "video/webm")}

class AttachmentError(Exception):
    def __init__(self, code, message, http_status=400):
        super().__init__(message); self.code = code; self.http_status = http_status

@dataclass(frozen=True)
class AttachmentLimits:
    image_bytes: int = 10 << 20
    video_bytes: int = 50 << 20
    quota_bytes: int = 100 << 20
    max_attachments: int = 16
    ttl_seconds: float = 900
    upload_seconds: float = 30
    chunk_bytes: int = 65536

    def __post_init__(self):
        for name, maximum in (("image_bytes",512<<20), ("video_bytes",512<<20), ("quota_bytes",1<<30),
                              ("max_attachments",128), ("ttl_seconds",86400), ("upload_seconds",120)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"Invalid attachment limit {name}")
            if name not in ("ttl_seconds", "upload_seconds") and type(value) is not int:
                raise ValueError(f"Integer attachment limit required: {name}")
        if type(self.chunk_bytes) is not int or self.chunk_bytes != 65536:
            raise ValueError("Attachment block size is fixed at 65536")

    def public_dict(self):
        return {**{name:getattr(self,name) for name in ("image_bytes","video_bytes","quota_bytes",
                "max_attachments","ttl_seconds","upload_seconds")}, "allowed_mime_types":MIME_TYPES}

@dataclass(frozen=True)
class UploadReservation:
    id: str

@dataclass
class _Record:
    attachment: Attachment
    stamp: SelectionStamp
    expires: float
    state: str = "uploading"
    cancelled: bool = False
    fd: int = -1
    inode: int = 0
    receiving: bool = False


def _format(prefix):
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if len(prefix)>=12 and prefix[:4]==b"RIFF" and prefix[8:12]==b"WEBP": return "image/webp"
    if len(prefix)>=16 and prefix[4:8]==b"ftyp" and prefix[8:12] in (b"isom",b"iso2",b"mp41",b"mp42",b"avc1",b"M4V ",b"qt  ",b"dash"):
        return "video/mp4"
    if prefix.startswith(b"\x1a\x45\xdf\xa3"): return "video/webm"
    return None


class AttachmentStore:
    def __init__(self, limits, parent_dir=None, clock=time.monotonic):
        self.limits, self.clock = limits, clock
        self.directory = Path(tempfile.mkdtemp(prefix="edge-ai-attachments-", dir=parent_dir))
        os.chmod(self.directory, 0o700)
        self._lock = threading.RLock(); self._records = {}; self._closing = False
        self._stop = threading.Event()
        self._maintenance = threading.Thread(target=self._maintain, daemon=True, name="attachment-expiry")
        self._maintenance.start()

    @property
    def reserved_bytes(self):
        with self._lock: return sum(r.attachment.size_bytes for r in self._records.values())

    @property
    def has_leases(self):
        with self._lock: return any(r.state == "leased" for r in self._records.values())

    def _maintain(self):
        while not self._stop.wait(min(5, self.limits.ttl_seconds)):
            self.expire()

    def reserve(self, stamp, kind, size, content_type):
        if kind not in MIME_TYPES or content_type not in MIME_TYPES[kind]:
            raise AttachmentError("unsupported_media", "请选择 JPEG、PNG、WebP 图片或 MP4、WebM 视频。", 415)
        if type(size) is not int or size <= 0 or size > getattr(self.limits, kind + "_bytes"):
            raise AttachmentError("attachment_too_large", "附件大小超过当前限制。", 413)
        with self._lock:
            if self._closing: raise AttachmentError("server_closing", "服务正在关闭。", 503)
            self.expire()
            if any(r.state == "uploading" for r in self._records.values()):
                raise AttachmentError("upload_busy", "请等待当前附件上传完成。", 409)
            if len(self._records) >= self.limits.max_attachments or self.reserved_bytes + size > self.limits.quota_bytes:
                raise AttachmentError("attachment_quota", "附件暂存空间已满，请移除附件后重试。", 413)
            identifier = uuid.uuid4().hex
            path = self.directory / identifier
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            self._records[identifier] = _Record(Attachment(identifier,kind,content_type,size,path), stamp,
                    self.clock()+self.limits.upload_seconds, fd=fd, inode=os.fstat(fd).st_ino)
            return UploadReservation(identifier)

    def receive(self, reservation, read_chunk, deadline):
        with self._lock:
            record = self._records.get(reservation.id)
            if record is None or record.state != "uploading":
                raise AttachmentError("invalid_attachment", "附件已失效，请重新选择。", 409)
            if record.receiving:
                raise AttachmentError("upload_busy", "附件正在上传。", 409)
            record.receiving=True
        remaining = record.attachment.size_bytes; prefix = bytearray()
        try:
            while remaining:
                with self._lock:
                    if record.cancelled or self._closing:
                        raise AttachmentError("upload_cancelled", "上传已取消。", 409)
                seconds = deadline - self.clock()
                if seconds <= 0: raise AttachmentError("upload_timeout", "附件上传超时，请重试。", 408)
                chunk = read_chunk(min(remaining,self.limits.chunk_bytes),seconds)
                if self.clock() >= deadline: raise AttachmentError("upload_timeout", "附件上传超时，请重试。", 408)
                if not isinstance(chunk,bytes) or not chunk or len(chunk)>min(remaining,self.limits.chunk_bytes):
                    raise AttachmentError("short_body", "附件上传未完成，请重试。")
                prefix.extend(chunk[:max(0,64-len(prefix))])
                view = memoryview(chunk)
                while view:
                    written = os.write(record.fd,view); view = view[written:]
                remaining -= len(chunk)
            if _format(prefix) != record.attachment.media_type:
                raise AttachmentError("invalid_format", "文件格式与声明类型不一致。", 415)
            with self._lock:
                if record.cancelled or self._closing:
                    raise AttachmentError("upload_cancelled", "上传已取消。", 409)
                os.close(record.fd); record.fd=-1
                record.state="idle"; record.expires=self.clock()+self.limits.ttl_seconds
                return record.attachment
        except BaseException:
            with self._lock: self._remove(record.attachment.id)
            raise

    def _get(self, identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r"[0-9a-f]{32}",identifier):
            raise AttachmentError("invalid_attachment", "附件编号无效。")
        record=self._records.get(identifier)
        if record is None: raise AttachmentError("expired_attachment", "附件已失效，请重新选择。", 409)
        return record

    def metadata(self, identifier):
        with self._lock:
            record=self._get(identifier); attachment=record.attachment
            return {"attachment_id":attachment.id,"kind":attachment.kind,"media_type":attachment.media_type,
                    "size":attachment.size_bytes,"expires":time.time()+max(0,record.expires-self.clock())}

    @contextmanager
    def consume(self, identifier, stamp):
        with self._lock:
            self.expire(); record=self._get(identifier)
            if self._closing or record.state != "idle" or record.stamp != stamp:
                raise AttachmentError("stale_attachment", "附件与当前对话不匹配，请重新选择。", 409)
            if self.has_leases: raise AttachmentError("backend_busy", "上一轮媒体交互尚未结束。", 409)
            try: info=record.attachment.local_path.lstat()
            except OSError: raise AttachmentError("invalid_attachment", "附件已失效，请重新选择。", 409)
            if not stat.S_ISREG(info.st_mode) or info.st_ino!=record.inode or info.st_size!=record.attachment.size_bytes:
                raise AttachmentError("invalid_attachment", "附件已失效，请重新选择。", 409)
            record.state="leased"
        try: yield record.attachment
        finally:
            with self._lock: self._remove(identifier)

    def _remove(self, identifier):
        record=self._records.pop(identifier,None)
        if record:
            if record.fd>=0: os.close(record.fd)
            record.attachment.local_path.unlink(missing_ok=True)
        if self._closing and not self._records and self.directory.exists():
            self.directory.rmdir()

    def abort_upload(self,reservation):
        with self._lock:
            record=self._records.get(reservation.id)
            if record and record.state=="uploading":
                record.cancelled=True
                if not record.receiving: self._remove(reservation.id)

    def discard(self, identifier):
        with self._lock:
            record=self._get(identifier)
            if record.state == "leased": return False
            if record.state == "uploading": record.cancelled=True
            else: self._remove(identifier)
            return True

    def invalidate_idle(self):
        with self._lock:
            for identifier,record in list(self._records.items()):
                if record.state == "uploading":
                    record.cancelled=True
                    if not record.receiving: self._remove(identifier)
                elif record.state == "idle": self._remove(identifier)

    def expire(self):
        with self._lock:
            expired=[key for key,r in self._records.items() if r.state=="idle" and r.expires<=self.clock()]
            for key,record in self._records.items():
                if record.state=="uploading" and record.expires<=self.clock():
                    record.cancelled=True
                    if not record.receiving: expired.append(key)
            for key in expired: self._remove(key)
            return len(expired)

    def close(self):
        self._stop.set()
        with self._lock:
            if self._closing: return
            self._closing=True
            self.invalidate_idle()
            if not self._records and self.directory.exists(): self.directory.rmdir()
        if threading.current_thread() is not self._maintenance: self._maintenance.join(timeout=1)
