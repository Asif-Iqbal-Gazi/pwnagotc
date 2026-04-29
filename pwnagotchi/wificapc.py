import json
import socket
import threading
import logging
import time


class WificapcClient:
    def __init__(self, socket_path='/run/wificapc.sock'):
        self._socket_path = socket_path
        self._sock = None
        # _lock guards _next_id, _pending, _running, and _sock reassignment.
        # _send_lock serializes write(2)s so two threads in cmd() can't
        # interleave bytes on the stream socket.
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._pending = {}
        self._event_handlers = {}
        self._running = False
        self._buf = b''
        self._reconnect_cb = None
        self._stop = False

    def connect(self):
        while not self._stop:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self._socket_path)
                with self._lock:
                    self._sock = sock
                    self._buf = b''
                    self._running = True
                threading.Thread(target=self._reader, daemon=True, name='WificapcReader').start()
                logging.info("connected to wificapc at %s", self._socket_path)
                return
            except Exception as e:
                logging.warning("waiting for wificapc (%s): %s", self._socket_path, e)
                time.sleep(1)

    def close(self):
        """Signal the client to stop reconnecting and tear down."""
        self._stop = True
        self._running = False
        try:
            if self._sock is not None:
                self._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    def on(self, event, callback):
        self._event_handlers.setdefault(event, []).append(callback)

    def on_reconnect(self, callback):
        self._reconnect_cb = callback

    def cmd(self, command, timeout=10, **params):
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            entry = {'evt': threading.Event(), 'result': None}
            self._pending[req_id] = entry
            sock = self._sock

        msg = {'id': req_id, 'cmd': command}
        if params:
            msg['args'] = params
        payload = (json.dumps(msg) + '\n').encode()
        try:
            # Serialise sends so concurrent callers can't interleave bytes
            # on the underlying SOCK_STREAM and produce malformed JSON.
            with self._send_lock:
                if sock is None:
                    raise ConnectionError("wificapc not connected")
                sock.sendall(payload)
        except Exception:
            with self._lock:
                self._pending.pop(req_id, None)
            raise

        try:
            if not entry['evt'].wait(timeout):
                raise TimeoutError("wificapc cmd '%s' timed out" % command)
            result = entry['result']
        finally:
            # Always reclaim the pending slot — on success the reader sets
            # the result but does not pop it (would race with cmd()'s read).
            with self._lock:
                self._pending.pop(req_id, None)

        if not result.get('ok'):
            raise RuntimeError("wificapc '%s' failed: %s" % (command, result.get('error', 'unknown')))
        return result.get('data', {})

    def _reader(self):
        while self._running:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    logging.warning("wificapc connection closed")
                    self._running = False
                    break
                self._buf += chunk
                while b'\n' in self._buf:
                    line, self._buf = self._buf.split(b'\n', 1)
                    line = line.strip()
                    if line:
                        self._dispatch(line.decode('utf-8', errors='replace'))
            except Exception as e:
                if self._running:
                    logging.error("wificapc reader: %s", e)
                break
        self._on_connection_lost()

    def _on_connection_lost(self):
        with self._lock:
            self._running = False
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            entry['result'] = {'ok': False, 'error': 'connection lost'}
            entry['evt'].set()
        if not self._stop:
            threading.Thread(target=self._reconnect_loop, daemon=True,
                             name='WificapcReconnect').start()

    def _reconnect_loop(self):
        if self._stop:
            return
        logging.info("wificapc reconnecting...")
        self.connect()
        if self._stop:
            return
        if self._reconnect_cb:
            try:
                self._reconnect_cb()
            except Exception as e:
                logging.error("wificapc reconnect callback: %s", e)

    def _dispatch(self, line):
        try:
            msg = json.loads(line)
        except Exception:
            logging.warning("wificapc unparseable: %s", line[:200])
            return

        if 'event' in msg:
            for cb in self._event_handlers.get(msg['event'], []):
                try:
                    cb(msg['event'], msg.get('data', {}))
                except Exception as e:
                    logging.error("wificapc event handler '%s': %s", msg['event'], e)
        elif 'id' in msg:
            with self._lock:
                entry = self._pending.get(msg['id'])
            if entry:
                entry['result'] = msg
                entry['evt'].set()
