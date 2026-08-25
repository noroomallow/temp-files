import socket
import struct
import subprocess

HOST = "0.0.0.0"
PORT = 5000


def recv_exact(sock, size):
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError("Client disconnected")

        data.extend(chunk)

    return bytes(data)


def recv_message(sock):
    header = recv_exact(sock, 4)
    size = struct.unpack("!I", header)[0]

    if size > 1024 * 1024:
        raise ValueError("Message too large")

    data = recv_exact(sock, size)
    return data.decode("utf-8", errors="replace")


def send_message(sock, message):
    data = message.encode("utf-8")
    header = struct.pack("!I", len(data))
    sock.sendall(header + data)


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(1)

print(f"Server listening on port {PORT}...")

conn, addr = server.accept()
print("Client connected:", addr)

try:
    while True:
        try:
            command = recv_message(conn)

        except ConnectionError:
            print("Client disconnected.")
            break

        if command.strip().lower() == "exit":
            break

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )

            output = result.stdout + result.stderr

            if not output:
                output = f"Command completed with exit code {result.returncode}\n"

        except subprocess.TimeoutExpired:
            output = "Command timed out after 10 seconds.\n"

        except Exception as e:
            output = f"Error: {e}\n"

        send_message(conn, output)

finally:
    conn.close()
    server.close()
    print("Server stopped.")
