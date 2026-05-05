import os
import re
import logging
import requests
import sqlite3
from datetime import datetime
from enum import Enum
from threading import Lock
from pwnagotchi.utils import remove_whitelisted
from pwnagotchi import plugins
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts


class WpaSec(plugins.Plugin):
    __author__ = '33197631+dadav@users.noreply.github.com'
    __version__ = '2.2.0'
    __license__ = 'GPL3'
    __description__ = 'This plugin automatically uploads handshakes to https://wpa-sec.stanev.org'
    
    class Status(Enum):
        TOUPLOAD = 0
        INVALID = 1
        SUCCESSFULL = 2

    def __init__(self):
        self.ready = False
        self.lock = Lock()
        
        self.options = dict()
        
        self._init_db()
        
    def _init_db(self):
        db_conn = sqlite3.connect('/etc/pwnagotchi/.wpa_sec_db')
        db_conn.execute('pragma journal_mode=wal')
        with db_conn:
            db_conn.execute('''
                CREATE TABLE IF NOT EXISTS handshakes (
                    path TEXT PRIMARY KEY,
                    status INTEGER
                )
            ''')
            db_conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_handshakes_status
                ON handshakes (status)
            ''')
        db_conn.close()

    def on_loaded(self):
        """
        Gets called when the plugin gets loaded
        """
        if 'api_key' not in self.options or ('api_key' in self.options and not self.options['api_key']):
            logging.error("WPA_SEC: API-KEY isn't set. Can't upload.")
            return

        if 'api_url' not in self.options or ('api_url' in self.options and not self.options['api_url']):
            logging.error("WPA_SEC: API-URL isn't set. Can't upload.")
            return

        self.skip_until_reload = set()

        self.ready = True
        logging.info("WPA_SEC: plugin loaded.")
        
    def on_handshake(self, agent, filename, access_point, client_station):
        config = agent.config()

        if not remove_whitelisted([filename], config['main']['whitelist']):
            return

        # wpa-sec.org runs hcxpcapngtool over uploads, which means it wants
        # raw frames (.pcap / .pcapng), not the .22000 hashcat hash. The
        # agent already prefers the daemon's .pcap path; only swap to a
        # sibling .pcap if we somehow received the .22000.
        if filename.endswith('.22000'):
            sibling_pcap = filename[:-len('.22000')] + '.pcap'
            upload_path = sibling_pcap if os.path.exists(sibling_pcap) else filename
        else:
            upload_path = filename

        db_conn = sqlite3.connect('/etc/pwnagotchi/.wpa_sec_db')
        with db_conn:
            db_conn.execute('''
                INSERT INTO handshakes (path, status)
                VALUES (?, ?)
                ON CONFLICT(path) DO UPDATE SET status = excluded.status
                WHERE handshakes.status = ?
            ''', (upload_path, self.Status.TOUPLOAD.value, self.Status.INVALID.value))
        db_conn.close()

    def on_internet_available(self, agent):
        """
        Called when there's internet connectivity
        """
        if not self.ready or self.lock.locked():
            return

        with self.lock:
            display = agent.view()
            
            try:
                db_conn = sqlite3.connect('/etc/pwnagotchi/.wpa_sec_db')
                cursor = db_conn.cursor()
                
                cursor.execute('SELECT path FROM handshakes WHERE status = ?', (self.Status.TOUPLOAD.value,))
                handshakes_toupload = [row[0] for row in cursor.fetchall()]
                handshakes_toupload = set(handshakes_toupload) - self.skip_until_reload

                if handshakes_toupload:
                    logging.info("WPA_SEC: Internet connectivity detected. Uploading new handshakes...")
                    for idx, handshake in enumerate(handshakes_toupload):
                        display.on_uploading(f"WPA-SEC ({idx + 1}/{len(handshakes_toupload)})")
                        logging.info("WPA_SEC: Uploading %s...", handshake)

                        try:
                            upload_response = self._upload_to_wpasec(handshake)

                            if upload_response.startswith("hcxpcapngtool"):
                                logging.info(f"WPA_SEC: {handshake} successfully uploaded.")
                                new_status = self.Status.SUCCESSFULL.value
                            else:
                                logging.info(f"WPA_SEC: {handshake} uploaded, but it was invalid.")
                                new_status = self.Status.INVALID.value

                            cursor.execute('''
                                INSERT INTO handshakes (path, status)
                                VALUES (?, ?)
                                ON CONFLICT(path) DO UPDATE SET status = excluded.status
                            ''', (handshake, new_status))
                            db_conn.commit()

                            # Optional cleanup: ask the daemon to delete the
                            # .pcap + .22000 for this pair, and drop the
                            # local DB row. Off by default — users running
                            # offline hashcat against the .22000 want the
                            # files to stick around. Only fires on a server
                            # ack ("hcxpcapngtool" response prefix).
                            if (new_status == self.Status.SUCCESSFULL.value and
                                    self.options.get('delete_after_upload')):
                                self._delete_pair(agent, handshake)
                                cursor.execute('DELETE FROM handshakes WHERE path = ?',
                                               (handshake,))
                                db_conn.commit()

                        except requests.exceptions.RequestException:
                            logging.exception("WPA_SEC: RequestException uploading %s, skipping until reload.", handshake)
                            self.skip_until_reload.add(handshake)
                        except OSError:
                            logging.exception("WPA_SEC: OSError uploading %s, deleting from db.", handshake)
                            cursor.execute('DELETE FROM handshakes WHERE path = ?', (handshake,))
                            db_conn.commit()
                        except Exception:
                            logging.exception("WPA_SEC: Exception uploading %s.", handshake)

                    display.on_normal()
                    
                cursor.close()
                db_conn.close()
            except Exception:
                logging.exception("WPA_SEC: Exception uploading results.")

            try:
                if 'download_results' in self.options and self.options['download_results']:
                    config = agent.config()
                    handshake_dir = config['wificapc']['handshakes']
                    
                    cracked_file_path = os.path.join(handshake_dir, 'wpa-sec.cracked.potfile')

                    if os.path.exists(cracked_file_path):
                        last_check = datetime.fromtimestamp(os.path.getmtime(cracked_file_path))
                        download_interval = int(self.options.get('download_interval', 3600))
                        if last_check is not None and ((datetime.now() - last_check).seconds / download_interval) < 1:
                            return

                    self._download_from_wpasec(cracked_file_path)
                    if 'single_files' in self.options and self.options['single_files']:
                        self._write_cracked_single_files(cracked_file_path, handshake_dir)
            except Exception:
                logging.exception("WPA_SEC: Exception downloading results.")

    @staticmethod
    def _pair_macs_from_path(path):
        """Recover (ap_bssid, sta_mac) from a wificapc-named handshake
        file. wificapc writes <dir>/<aphex>_<stahex>.{pcap,22000} where
        each hex is 12 lowercase chars. Returns (ap, sta) in colon-
        separated form, or (None, None) if the basename doesn't match."""
        name = os.path.basename(path)
        # strip extension
        for ext in ('.pcap', '.22000', '.pcapng'):
            if name.endswith(ext):
                name = name[:-len(ext)]
                break
        if '_' not in name:
            return None, None
        ap_hex, _, sta_hex = name.partition('_')
        if len(ap_hex) != 12 or len(sta_hex) != 12:
            return None, None
        try:
            int(ap_hex, 16)
            int(sta_hex, 16)
        except ValueError:
            return None, None
        fmt = lambda h: ':'.join(h[i:i+2] for i in range(0, 12, 2))
        return fmt(ap_hex), fmt(sta_hex)

    def _delete_pair(self, agent, handshake_path):
        """Ask the wificapc daemon to remove the .pcap + .22000 for the
        (ap, sta) encoded in `handshake_path`. Best-effort: if the
        daemon doesn't support delete_handshake (older release), we
        log and move on rather than aborting the whole upload run."""
        ap, sta = self._pair_macs_from_path(handshake_path)
        if not ap or not sta:
            logging.debug("WPA_SEC: cannot derive ap/sta from %s, skipping delete",
                          handshake_path)
            return
        try:
            res = agent._wificapc.cmd("delete_handshake", ap_bssid=ap, sta_mac=sta)
            logging.info("WPA_SEC: deleted %s/%s (removed=%s)",
                         ap, sta, res.get("removed", "?"))
        except Exception as e:
            logging.warning("WPA_SEC: delete_handshake(%s,%s) failed: %s",
                            ap, sta, e)

    def _upload_to_wpasec(self, path, timeout=30):
        """
        Uploads the file to wpasec
        """
        with open(path, 'rb') as file_to_upload:
            cookie = {'key': self.options['api_key']}
            payload = {'file': file_to_upload}
            headers = {"HTTP_USER_AGENT": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:15.0) Gecko/20100101 Firefox/15.0.1"}

            result = requests.post(
                self.options['api_url'],
                cookies=cookie,
                files=payload,
                headers=headers,
                timeout=timeout
            )
            result.raise_for_status()
            
            response = result.text.partition('\n')[0]

            logging.debug("WPA_SEC: Response uploading %s: %s.", path, response)

            return response

    def _download_from_wpasec(self, output, timeout=30):
        """
        Downloads the results from wpasec and saves them to output

        Output-Format: bssid, station_mac, ssid, password
        """
        api_url = self.options['api_url']
        if not api_url.endswith('/'):
            api_url = f"{api_url}/"
        api_url = f"{api_url}?api&dl=1"

        cookie = {'key': self.options['api_key']}
        headers = {"HTTP_USER_AGENT": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:15.0) Gecko/20100101 Firefox/15.0.1"}

        logging.info("WPA_SEC: Downloading cracked passwords...")

        result = requests.get(api_url, cookies=cookie, headers=headers, timeout=timeout)
        result.raise_for_status()

        with open(output, 'wb') as output_file:
            output_file.write(result.content)

        logging.info("WPA_SEC: Downloaded cracked passwords.")

    def _write_cracked_single_files(self, cracked_file_path, handshake_dir):
        """
        Splits download results from wpasec into individual .pcap.cracked files in handshake_dir

        Each .pcap.cracked file will contain the cracked handshake password
        """
        logging.info("WPA_SEC: Writing cracked single files...")

        with open(cracked_file_path, 'r') as cracked_file:
            for line in cracked_file:
                try:
                    bssid,station_mac,ssid,password = line.split(":")
                    if password:
                        handshake_filename = re.sub(r'[^a-zA-Z0-9]', '', ssid) + '_' + bssid
                        # match against .22000 if present, otherwise .pcap
                        hs_22000 = os.path.join(handshake_dir, handshake_filename + '.22000')
                        hs_pcap  = os.path.join(handshake_dir, handshake_filename + '.pcap')
                        hs_path  = hs_22000 if os.path.exists(hs_22000) else hs_pcap
                        cracked_path = hs_path + '.cracked'
                        if os.path.exists(hs_path) and not os.path.exists(cracked_path):
                            with open(cracked_path, 'w') as f:
                                f.write(password)
                except Exception:
                    logging.exception(f"WPA_SEC: Exception writing cracked single file, parsing line {line}.")
    
        logging.info("WPA_SEC: Wrote cracked single files.")

    def on_webhook(self, path, request):
        from flask import make_response

        html_content = f'''
            <html>
                <body>
                    <form id="postForm" action="{self.options['api_url']}" method="POST">
                        <input type="hidden" name="key" value="{self.options['api_key']}">
                    </form>
                    <script type="text/javascript">
                        document.getElementById('postForm').submit();
                    </script>
                </body>
            </html>
        '''
        
        return make_response(html_content)

    def on_ui_setup(self, ui):
        if 'show_pwd' in self.options and self.options['show_pwd'] and 'download_results' in self.options and self.options['download_results']:
            # Setup for horizontal orientation with adjustable positions
            x_position = 0  # X position for both SSID and password
            ssid_y_position = 95  # Y position for SSID
            ssid_position = (x_position, ssid_y_position)
            ui.add_element('pass', LabeledValue(color=BLACK, label='', value='', position=ssid_position,
                                                label_font=fonts.Bold, text_font=fonts.Small))

    def on_unload(self, ui):
        with ui._lock:
            ui.remove_element('pass')

    def on_ui_update(self, ui):
        if 'show_pwd' in self.options and self.options['show_pwd'] and 'download_results' in self.options and self.options['download_results']:
            file_path = '/etc/pwnagotchi/handshakes/wpa-sec.cracked.potfile'
            try:
                with open(file_path, 'r') as file:
                    # Read all lines and extract the required fields
                    lines = file.readlines()
                    if lines:  # Check if file is not empty
                        last_line = lines[-1]
                        parts = last_line.split(':')  # Split line into fields using ':' as a delimiter
                        if len(parts) >= 4:
                            result = f"{parts[2]} - {parts[3].strip()}"
                        else:
                            result = "Malformed line format"
                    else:
                        result = "File is empty"
            except FileNotFoundError:
                result = "File not found"
            except OSError as e:
                result = f"Error reading file: {e}"
            ui.set('pass', result)
