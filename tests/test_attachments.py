"""Small header fixtures exercise bounded storage, not media decoding."""
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

PNG = b"\x89PNG\r\n\x1a\n" + b"small fixture"
FIXTURES = [("image", "image/png", PNG), ("image", "image/jpeg", b"\xff\xd8\xfffixture"),
            ("image", "image/webp", b"RIFF\x10\x00\x00\x00WEBPfixture"),
            ("video", "video/mp4", b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42"),
            ("video", "video/webm", b"\x1a\x45\xdf\xa3fixture")]

class AttachmentTests(unittest.TestCase):
    def setUp(self):
        from ui_backend.attachments import AttachmentLimits, AttachmentStore
        from ui_backend.media import SelectionStamp
        self.now = [100.0]; self.stamp = SelectionStamp("a", "epoch", 1)
        self.parent = tempfile.TemporaryDirectory(); self.addCleanup(self.parent.cleanup)
        self.store = AttachmentStore(AttachmentLimits(), Path(self.parent.name), clock=lambda: self.now[0])
        self.addCleanup(self.store.close)

    def upload(self, kind="image", mime="image/png", body=PNG):
        import io
        reservation = self.store.reserve(self.stamp, kind, len(body), mime)
        stream = io.BytesIO(body)
        return self.store.receive(reservation, lambda size, remaining: stream.read(size), self.now[0]+30)

    def test_supported_formats_are_private_and_single_use(self):
        from ui_backend.attachments import AttachmentError
        for kind, mime, body in FIXTURES:
            attachment = self.upload(kind, mime, body)
            self.assertEqual(attachment.local_path.read_bytes(), body)
            self.assertEqual(attachment.local_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(attachment.local_path.parent.stat().st_mode & 0o777, 0o700)
            with self.store.consume(attachment.id, self.stamp) as leased:
                self.assertEqual(leased.kind, kind)
            self.assertFalse(attachment.local_path.exists())
            with self.assertRaises(AttachmentError):
                with self.store.consume(attachment.id, self.stamp): pass

    def test_invalid_limits_rejected(self):
        from ui_backend.attachments import AttachmentLimits
        for fields in ({"image_bytes":0}, {"video_bytes":True}, {"ttl_seconds":float("nan")},
                       {"upload_seconds":float("inf")}, {"max_attachments":129},
                       {"quota_bytes":(1<<30)+1}, {"image_bytes":(512<<20)+1}, {"chunk_bytes":1}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): AttachmentLimits(**fields)

    def test_mime_mismatch_svg_short_body_and_deadline_cleanup(self):
        from ui_backend.attachments import AttachmentError
        for mime, body in (("image/png", b"<svg/>"), ("image/jpeg", PNG), ("image/svg+xml", b"<svg/>")):
            with self.subTest(mime=mime), self.assertRaises(AttachmentError): self.upload(mime=mime, body=body)
        reservation = self.store.reserve(self.stamp, "image", 20, "image/png")
        with self.assertRaises(AttachmentError): self.store.receive(reservation, lambda n,t: b"", 130)
        reservation = self.store.reserve(self.stamp, "image", len(PNG), "image/png")
        def late(n, remaining): self.now[0] += 31; return PNG
        with self.assertRaises(AttachmentError): self.store.receive(reservation, late, 130)
        self.assertEqual(self.store.reserved_bytes, 0)

    def test_cancel_upload_and_second_upload_rejected(self):
        from ui_backend.attachments import AttachmentError
        reservation = self.store.reserve(self.stamp, "image", len(PNG), "image/png")
        with self.assertRaises(AttachmentError): self.store.reserve(self.stamp, "image", len(PNG), "image/png")
        self.store.invalidate_idle()
        with self.assertRaises(AttachmentError): self.store.receive(reservation, lambda n,t: PNG, 130)
        self.assertEqual(self.store.reserved_bytes, 0)

    def test_lease_is_not_removed_by_expiry_discard_or_close(self):
        attachment = self.upload()
        with self.store.consume(attachment.id, self.stamp):
            self.now[0] += 1000
            self.assertFalse(self.store.discard(attachment.id))
            self.store.expire(); self.store.close()
            self.assertTrue(attachment.local_path.exists())
        self.assertFalse(attachment.local_path.exists())
        self.assertFalse(self.store.directory.exists())

    def test_stamp_count_quota_expiry_and_symlink(self):
        from ui_backend.attachments import AttachmentError, AttachmentLimits, AttachmentStore
        from ui_backend.media import SelectionStamp
        attachment = self.upload()
        for stamp in (SelectionStamp("b", "epoch", 1), SelectionStamp("a", "epoch", 3), SelectionStamp("a", "new", 1)):
            with self.assertRaises(AttachmentError):
                with self.store.consume(attachment.id, stamp): pass
        attachment.local_path.unlink(); attachment.local_path.symlink_to("/dev/null")
        with self.assertRaises(AttachmentError):
            with self.store.consume(attachment.id, self.stamp): pass
        self.store.discard(attachment.id)
        small = AttachmentStore(AttachmentLimits(quota_bytes=len(PNG), max_attachments=1), Path(self.parent.name), clock=lambda:self.now[0])
        self.addCleanup(small.close)
        self.store.close(); self.store = small
        attachment = self.upload()
        with self.assertRaises(AttachmentError): self.upload()
        self.now[0] += 901
        self.assertEqual(small.expire(), 1)
        self.assertFalse(attachment.local_path.exists())
