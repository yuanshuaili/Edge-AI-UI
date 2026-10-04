"""Launch the lightweight exhibit UI without importing model dependencies."""

import argparse
import logging
import signal
from pathlib import Path

from ui_backend.server import create_server
from ui_backend.voice_protocol import DEFAULT_ASR_SOCKET
from ui_backend.attachments import AttachmentLimits


DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "backends.json"


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    def terminate(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description="Edge AI exhibition UI")
    parser.add_argument("--host", default="127.0.0.1", help="UI bind address; use a trusted interface address for LAN access")
    parser.add_argument("--port", type=int, default=8080, help="UI HTTP port")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="backend declarations JSON")
    parser.add_argument("--assistant-config", type=Path, help="deployment assistant profile JSON")
    parser.add_argument("--model-log-dir", type=Path, help="owned model development log directory")
    parser.add_argument("--allowed-host", action="append", default=[], help="additional UI hostname, e.g. jetson.local")
    parser.add_argument("--asr-socket", type=Path, default=DEFAULT_ASR_SOCKET,
                        help="local Unix socket for one persistent ASR process")
    parser.add_argument("--max-listen-seconds", type=int, default=20,
                        help="maximum microphone capture time per turn")
    parser.add_argument("--max-image-mib", type=int, default=10)
    parser.add_argument("--max-video-mib", type=int, default=50)
    parser.add_argument("--attachment-quota-mib", type=int, default=100)
    parser.add_argument("--max-attachments", type=int, default=16)
    parser.add_argument("--attachment-ttl-seconds", type=float, default=900)
    parser.add_argument("--upload-timeout-seconds", type=float, default=30)
    parser.add_argument("--attachment-parent-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        limits=AttachmentLimits(image_bytes=args.max_image_mib<<20,video_bytes=args.max_video_mib<<20,
            quota_bytes=args.attachment_quota_mib<<20,max_attachments=args.max_attachments,
            ttl_seconds=args.attachment_ttl_seconds,upload_seconds=args.upload_timeout_seconds)
    except ValueError as exc: parser.error(str(exc))
    with create_server(args.config, args.host, args.port, args.allowed_host,
                       asr_socket_path=args.asr_socket,
                       assistant_path=args.assistant_config,
                       model_log_dir=args.model_log_dir,
                       attachment_limits=limits, attachment_parent_dir=args.attachment_parent_dir,
                       max_listen_seconds=args.max_listen_seconds) as server:
        print(f"Edge AI UI: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nUI stopped.", flush=True)


if __name__ == "__main__":
    main()
