import socket
import subprocess

SERVER_IP = "10.70.91.189"
SERVER_PORT = 5000

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

print("Connecting to server...")

client.connect((SERVER_IP, SERVER_PORT))

print("Connected to server.")
print("Waiting for commands...\n")


COMMANDS = {
    "whoami": ["whoami"],
    "hostname": ["hostname"],
    "ipconfig": ["ipconfig"],
    "systeminfo": ["systeminfo"],
    "tasklist": ["tasklist"],
    "dir": ["cmd", "/c", "dir"],
    "python-version": ["python", "--version"],
}


while True:

    command = client.recv(4096).decode("utf-8").strip()

    if not command:
        break

    print(f"Received command: {command}")

    if command.lower() == "exit":
        print("Server closed the connection.")
        break

    if command not in COMMANDS:
        message = (
            "Command not allowed.\n"
            "Available commands: "
            + ", ".join(COMMANDS.keys())
        )

        client.sendall(message.encode("utf-8"))
        continue

    try:

        result = subprocess.run(
            COMMANDS[command],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False
        )

        output = result.stdout

        if result.stderr:
            output += "\n" + result.stderr

        if not output:
            output = "Command completed successfully."

        client.sendall(output.encode("utf-8"))

    except subprocess.TimeoutExpired:

        client.sendall(
            b"Error: command timed out."
        )

    except Exception as error:

        client.sendall(
            f"Error: {error}".encode("utf-8")
        )


client.close()

print("Client stopped.")
