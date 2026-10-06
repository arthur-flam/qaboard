"""Forwards a TCP port to another: lets the demo say http://qaboard.acme.internal (port 80) for a server on :5151."""
import socket
import threading


class Forward:
  def __init__(self, listen_port: int, target_port: int, host: str = '127.0.0.1'):
    self.target = (host, target_port)
    self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.server.bind((host, listen_port))
    self.server.listen(64)
    self.running = True
    threading.Thread(target=self._accept, daemon=True).start()

  def _accept(self):
    while self.running:
      try:
        client, _ = self.server.accept()
      except OSError:
        return
      try:
        upstream = socket.create_connection(self.target)
      except OSError:
        client.close()
        continue
      for a, b in ((client, upstream), (upstream, client)):
        threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

  @staticmethod
  def _pipe(source, destination):
    try:
      while True:
        data = source.recv(65536)
        if not data:
          break
        destination.sendall(data)
    except OSError:
      pass
    finally:
      for s in (source, destination):
        try:
          s.shutdown(socket.SHUT_RDWR)
        except OSError:
          pass

  def close(self):
    self.running = False
    self.server.close()
