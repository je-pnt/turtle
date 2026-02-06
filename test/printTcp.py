import socket

HOST = "127.0.0.1"
PORT = 81

conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
conn.connect((HOST, PORT))

while True:
    data = conn.recv(4096)
    if not data:
        break
    print("Received message:", data.decode(errors="replace"), end="")
