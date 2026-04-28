import json
import socket
import threading
import logging
import time


class WificapcClient:
    def __init__(self, socket_path='/run/wificapc.sock'):
        self._socket_path = socket_path
        self._sock = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending = {}
        self._event_handlers = {}
        self._running = False
        self._buf = b''
        self._reconnect_cb = None

    def connect(self):
        while True:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self._socket_path)
                self._sock = sock
                self._buf = b''
                self._running = True
                threading.Thread(target=self._reader, daemon=True, name='WificapcReader').start()
                logging.info("connected to wificapc at %s", self._socket_path)
                return
            except Exception as e:
                logging.warning("waiting for wificapc (%s): %s", self._socket_path, e)
                time.sleep(1)

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

        msg = {'id': req_id, 'cmd': command}
        if params:
            msg['args'] = params
        try:
            self._sock.sendall((json.dumps(msg) + '\n').encode())
        except Exception:
            with self._lock:
                self._pending.pop(req_id, None)
            raise

        if not entry['evt'].wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise TimeoutError("wificapc cmd '%s' timed out" % command)

        result = entry['result']
        if not result.get('ok'):
            raise Exception("wificapc '%s' failed: %s" % (command, result.get('error', 'unknown')))
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
        self._running = False
        with self._lock:
            for entry in self._pending.values():
                entry['result'] = {'ok': False, 'error': 'connection lost'}
                entry['evt'].set()
            self._pending.clear()
        threading.Thread(target=self._reconnect_loop, daemon=True,
                         name='WificapcReconnect').start()

    def _reconnect_loop(self):
        logging.info("wificapc reconnecting...")
        self.connect()
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
