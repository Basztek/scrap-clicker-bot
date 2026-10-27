import socket
import subprocess
import threading


class VideoStream(threading.Thread):
    # Serves the phone's screen (h264 from adb screenrecord) on 127.0.0.1:<port>.
    # Each client connection gets a fresh screenrecord process, so when it ends
    # (screenrecord stops after ~3 minutes) the client just reconnects.
    host = '127.0.0.1'
    port = None
    size = None
    time_limit = None

    def __init__(self, port, size='480x640', time_limit=180):
        self.port = int(port)
        self.size = size
        self.time_limit = time_limit
        threading.Thread.__init__(self, daemon=True)

    def initialize_video(self):
        return subprocess.Popen(
            ["adb", "exec-out", "screenrecord", "--bit-rate=8m", "--output-format=h264",
             "--size=" + self.size, "--time-limit=%d" % self.time_limit, "-"],
            stdout=subprocess.PIPE, shell=False
        )

    def stream_to_client(self, client):
        video = self.initialize_video()
        try:
            while True:
                chunk = video.stdout.read1(65536)
                if not chunk:
                    break
                client.sendall(chunk)
        except OSError:
            pass
        finally:
            video.kill()
            video.wait()
            client.close()

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(1)
        while True:
            client, _ = server.accept()
            self.stream_to_client(client)
