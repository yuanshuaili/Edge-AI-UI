"""Launch the lightweight exhibit UI without importing model dependencies."""

import argparse
import logging
import signal
from pathlib import Path

from ui_backend.server import create_server
from ui_backend.voice_protocol import DEFAULT_ASR_SOCKET


DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "backends.json"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    def terminate(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description="Edge AI exhibition UI")
    parser.add_argument("--host", default="127.0.0.1", help="UI bind address; use a trusted interface address for LAN access")
    parser.add_argument("--port", type=int, default=8080, help="UI HTTP port")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="backend declarations JSON")
    parser.add_argument("--allowed-host", action="append", default=[], help="additional UI hostname, e.g. jetson.local")
    parser.add_argument("--asr-socket", type=Path, default=DEFAULT_ASR_SOCKET,
                        help="local Unix socket for one persistent ASR process")
    parser.add_argument("--max-listen-seconds", type=int, default=20,
                        help="maximum microphone capture time per turn")
    args = parser.parse_args()
    with create_server(args.config, args.host, args.port, args.allowed_host,
                       asr_socket_path=args.asr_socket,
                       max_listen_seconds=args.max_listen_seconds) as server:
        print(f"Edge AI UI: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nUI stopped.", flush=True)


if __name__ == "__main__":
    main()
