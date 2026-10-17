import hashlib
import socket
from uuid import uuid4

from tests.durable_support import DurableServer


def test_real_interrupted_http_body_then_kill_restart_after_ack(tmp_path):
    server = DurableServer(tmp_path)
    try:
        server.start()
        chunk = b"a" * 65536
        response = server.client.post("/api/v1/uploads", json={"filename": "fixture.zip", "size_bytes": 2 * len(chunk)},
            headers={"X-Owner-Key": server.key, "Idempotency-Key": str(uuid4())})
        assert response.status_code == 201
        upload_id = response.json()["upload_id"]
        headers = server.headers(upload_id)
        sha = hashlib.sha256(chunk).hexdigest()
        # Real socket sends only a prefix of the declared body, then disconnects.
        with socket.create_connection(("127.0.0.1", 8011), timeout=10) as connection:
            lines = [f"PUT /api/v1/uploads/{upload_id}/chunks/0 HTTP/1.1", "Host: 127.0.0.1:8011",
                "Content-Type: application/octet-stream", "Content-Length: 65536", "Upload-Offset: 0",
                "X-Chunk-SHA256: " + sha, *[key + ": " + value for key, value in headers.items()], "", ""]
            connection.sendall("\r\n".join(lines).encode() + chunk[:4096])
        url = "/api/v1/uploads/" + upload_id
        reply = server.client.put(url + "/chunks/0", content=chunk,
            headers={**headers, "Upload-Offset": "0", "X-Chunk-SHA256": sha})
        assert reply.status_code == 200 and reply.json()["received_bytes"] == 65536
        server.restart()  # Actual OS process kill, no graceful lifespan cleanup.
        recovered = server.client.get(url, headers=headers)
        assert recovered.json()["received_bytes"] == 65536 and recovered.json()["durable"]
        replay = server.client.put(url + "/chunks/0", content=chunk,
            headers={**headers, "Upload-Offset": "0", "X-Chunk-SHA256": sha})
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        reply = server.client.put(url + "/chunks/1", content=chunk,
            headers={**headers, "Upload-Offset": "65536", "X-Chunk-SHA256": sha})
        assert reply.json()["received_bytes"] == 131072
        done = server.client.post(url + "/complete", headers=headers).json()
        assert done["state"] == "uploaded" and not done["ready"] and not done["validated"]
        with (server.root / "uploads" / upload_id / "payload.part").open("rb") as file:
            assert hashlib.file_digest(file, "sha256").hexdigest() == hashlib.sha256(chunk + chunk).hexdigest()
    finally:
        server.close()
