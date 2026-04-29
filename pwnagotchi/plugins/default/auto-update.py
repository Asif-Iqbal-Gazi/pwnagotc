import os
import re
import logging
import subprocess
import requests
import platform
import shutil
import glob
from threading import Lock
import time

import pwnagotchi
import pwnagotchi.plugins as plugins
from pwnagotchi.utils import StatusFile, parse_version as version_to_tuple


# ---------------------------------------------------------------------------
# Per-target failure isolation: each entry in TARGETS is checked and
# installed independently. A network blip on one repo, or a botched
# install of one component, must not cascade and prevent the other
# component from updating. Targets are described declaratively; the
# main loop just iterates and catches exceptions per entry.
# ---------------------------------------------------------------------------


def fetch_latest_release(repo, token=""):
    """Fetch the latest GitHub release JSON for `repo`. Raises on
    network/API errors so the caller can isolate per-target."""
    headers = {}
    if token:
        headers['Authorization'] = f'token {token}'
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    remaining = resp.headers.get('X-RateLimit-Remaining')
    if remaining is not None:
        logging.debug("[update] %s: rate-limit remaining=%s", repo, remaining)
    return resp.json()


def _find_native_zip(latest, arch):
    """Pick a release asset that's an arch-tagged .zip (e.g. WiFiCapC's
    wificapc-X.Y.Z-aarch64.zip). Returns the download URL or None."""
    is_armhf = arch.startswith('arm') and not arch.startswith('aarch')
    is_aarch = arch.startswith('aarch')
    for asset in latest.get('assets', []):
        name = asset.get('name', '')
        if not name.endswith('.zip'):
            continue
        if arch in name:
            return asset['browser_download_url']
        if is_aarch and 'aarch' in name:
            return asset['browser_download_url']
        if is_armhf and 'armhf' in name:
            return asset['browser_download_url']
    return None


def _find_wheel(latest):
    """Pick a .whl asset. Pure-Python wheels are arch-agnostic so any
    py3-none-any.whl works. Returns the download URL or None."""
    for asset in latest.get('assets', []):
        name = asset.get('name', '')
        if name.endswith('.whl'):
            return asset['browser_download_url']
    return None


def check(target, token=""):
    """Resolve update info for one target. Returns a dict with at
    least 'current', 'available', 'url' (None if up-to-date or no
    matching asset), 'kind', and the target's static fields merged in.
    Raises on network/API errors — caller wraps."""
    info = {
        'repo': target['repo'],
        'name': target['name'],
        'service': target.get('service'),
        'kind': target['kind'],
        'binary': target.get('binary'),
        'current': target['current'],
        'available': None,
        'url': None,
        'arch': platform.machine(),
    }
    latest = fetch_latest_release(target['repo'], token)
    info['available'] = latest['tag_name'].lstrip('v')

    if version_to_tuple(info['available']) <= version_to_tuple(info['current']):
        return info

    if target['kind'] == 'native-binary':
        info['url'] = _find_native_zip(latest, info['arch'])
    elif target['kind'] == 'wheel':
        info['url'] = _find_wheel(latest)
        # Fall back to source archive if no wheel was attached to the
        # release (older releases predate the workflow that uploads .whl).
        if not info['url']:
            info['url'] = (
                f"https://github.com/{target['repo']}/archive/"
                f"{latest['tag_name']}.zip"
            )
            info['kind'] = 'source-archive'
    elif target['kind'] == 'source-archive':
        info['url'] = (
            f"https://github.com/{target['repo']}/archive/"
            f"{latest['tag_name']}.zip"
        )
    return info


# ---------------------------------------------------------------------------
# Helpers shared across install paths.
# ---------------------------------------------------------------------------


def make_path_for(name):
    path = os.path.join("/opt/", name)
    if os.path.exists(path):
        logging.debug("[update] deleting %s", path)
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def _download(url, target_path, display, label):
    logging.info("[update] downloading %s -> %s", url, target_path)
    if display:
        display.update(force=True, new_data={'status': f'Downloading {label} ...'})
    rc = subprocess.run(
        ['wget', '-q', '-O', target_path, url],
        check=False,
    ).returncode
    if rc != 0 or not os.path.exists(target_path) or os.path.getsize(target_path) == 0:
        raise RuntimeError(f"download failed for {url} (rc={rc})")


def _verify_native_zip(name, path, source_path, display, update):
    """Best-effort sha256 check against any *.sha256 file shipped in the
    zip. Returns True if check passed or no checksum was provided
    (consistent with the historical behavior). Returns False on
    mismatch — the caller treats that as a fatal install error."""
    if display:
        display.update(force=True, new_data={
            'status': f'Verifying {name} {update["available"]} ...'
        })
    checksums = glob.glob(os.path.join(path, "*.sha256"))
    if not checksums:
        # Native binaries should ship a checksum; if missing we refuse
        # rather than silently install an unverified blob.
        logging.warning("[update] %s: no SHA256 checksum file shipped", name)
        return False
    with open(checksums[0], 'rt') as fp:
        expected = fp.read().split('=')[-1].strip().split()[0].lower()
    real = subprocess.getoutput(f'sha256sum "{source_path}"').split(' ')[0].strip().lower()
    if real != expected:
        logging.warning("[update] %s: sha256 mismatch (got %s, want %s)",
                        name, real, expected)
        return False
    return True


# ---------------------------------------------------------------------------
# Per-kind install paths. Each must raise on failure so the main loop
# can isolate per-target.
# ---------------------------------------------------------------------------


def install_native_binary(update, display):
    """Stops the systemd unit, replaces the binary, restarts the unit."""
    name = update['repo'].split('/')[1]
    binary = update.get('binary') or name.lower()
    path = make_path_for(name)
    target = os.path.join(path, f"{name}_{update['available']}.zip")
    _download(update['url'], target, display, f"{name} {update['available']}")

    if display:
        display.update(force=True, new_data={
            'status': f'Extracting {name} {update["available"]} ...'
        })
    rc = subprocess.run(['unzip', '-o', target, '-d', path], check=False).returncode
    if rc != 0:
        raise RuntimeError(f"unzip failed for {target} (rc={rc})")

    source_path = os.path.join(path, binary)
    if not _verify_native_zip(name, path, source_path, display, update):
        raise RuntimeError(f"checksum verification failed for {name}")

    dest_path = subprocess.getoutput(f"which {binary}").strip()
    if not dest_path:
        raise RuntimeError(f"can't locate install path for {binary}")

    if display:
        display.update(force=True, new_data={
            'status': f'Installing {name} {update["available"]} ...'
        })
    logging.info("[update] %s: stopping service %s", name, update['service'])
    subprocess.run(['service', update['service'], 'stop'], check=False)
    shutil.move(source_path, dest_path)
    os.chmod(dest_path, 0o755)
    logging.info("[update] %s: starting service %s", name, update['service'])
    subprocess.run(['service', update['service'], 'start'], check=False)


def install_wheel(update, display):
    """Pip-install a Python wheel into the deployed venv."""
    name = update['repo'].split('/')[1]
    path = make_path_for(name)
    wheel_name = os.path.basename(update['url'].split('?')[0])
    target = os.path.join(path, wheel_name)
    _download(update['url'], target, display, f"{name} {update['available']}")

    if display:
        display.update(force=True, new_data={
            'status': f'Installing {name} {update["available"]} ...'
        })
    # --force-reinstall ensures the version actually replaces the
    # currently-installed one even when pip thinks the package is up
    # to date (e.g. running off a tagged checkout vs the released tag).
    subprocess.run(
        ["bash", "-c",
         f"source /opt/.pwn/bin/activate && pip install --force-reinstall '{target}'"],
        check=True,
    )
    shutil.rmtree(path, ignore_errors=True)


def install_source_archive(update, display):
    """Fallback path: download the GitHub auto-archive zip, unpack,
    pip-install the resulting source directory.

    TODO(pwnagotchi self-update): this assumes /opt/.pwn is the venv
    and that the repo's pyproject.toml package name matches the
    running module ("pwnagotchi" vs the repo "pwnagotc"). Both hold
    for the current image build but should be made explicit before
    users on non-standard layouts can rely on it.
    """
    name = update['repo'].split('/')[1]
    path = make_path_for(name)
    target = os.path.join(path, f"{name}_{update['available']}.zip")
    _download(update['url'], target, display, f"{name} {update['available']}")

    if display:
        display.update(force=True, new_data={
            'status': f'Extracting {name} {update["available"]} ...'
        })
    rc = subprocess.run(['unzip', '-o', target, '-d', path], check=False).returncode
    if rc != 0:
        raise RuntimeError(f"unzip failed for {target} (rc={rc})")

    source_path = os.path.join(path, name)
    if not os.path.exists(source_path):
        # GitHub auto-archives unpack to <repo>-<tag-without-v>/
        source_path = f"{source_path}-{update['available']}"

    if display:
        display.update(force=True, new_data={
            'status': f'Installing {name} {update["available"]} ...'
        })
    subprocess.run(
        ["bash", "-c",
         f"source /opt/.pwn/bin/activate && pip install --force-reinstall '{source_path}'"],
        check=True,
    )
    shutil.rmtree(source_path, ignore_errors=True)


_INSTALLERS = {
    'native-binary': install_native_binary,
    'wheel': install_wheel,
    'source-archive': install_source_archive,
}


# ---------------------------------------------------------------------------
# Local version helpers.
# ---------------------------------------------------------------------------


def parse_version(cmd):
    out = subprocess.getoutput(cmd)
    for part in out.split(' '):
        part = part.replace('v', '').strip()
        if re.search(r'^\d+\.\d+\.\d+.*$', part):
            return part
    raise Exception(f'could not parse version from "{cmd}": output=\n{out}')


def wificapc_version():
    try:
        return parse_version('wificapc -V')
    except Exception:
        return '0.0.0'


# ---------------------------------------------------------------------------
# Plugin.
# ---------------------------------------------------------------------------


class AutoUpdate(plugins.Plugin):
    __author__ = 'evilsocket@gmail.com'
    __version__ = '1.2.0'
    __name__ = 'auto-update'
    __license__ = 'GPL3'
    __description__ = (
        'Checks GitHub for new releases of pwnagotchi and WiFiCapC, '
        'and applies them when internet is available. Each component '
        'is checked and installed independently — a failure on one '
        'never blocks the other.'
    )

    def __init__(self):
        self.ready = False
        self.status = StatusFile('/root/.auto-update')
        self.lock = Lock()
        self.options = dict()

    def on_loaded(self):
        if not self.options.get('interval'):
            logging.error("[update] main.plugins.auto-update.interval is not set")
            return
        self.ready = True
        logging.info("[update] plugin loaded.")

    def _targets(self):
        return [
            {
                'name': 'pwnagotchi',
                'repo': 'Asif-Iqbal-Gazi/pwnagotc',
                'service': 'pwnagotchi',
                'kind': 'wheel',
                'current': pwnagotchi.__version__,
            },
            {
                'name': 'WiFiCapC',
                'repo': 'Asif-Iqbal-Gazi/WiFiCapC',
                'service': 'wificapc',
                'kind': 'native-binary',
                'binary': 'wificapc',
                'current': wificapc_version(),
            },
        ]

    def on_internet_available(self, agent):
        if self.lock.locked():
            return
        with self.lock:
            if not self.ready:
                return
            if self.status.newer_then_hours(self.options['interval']):
                logging.debug(
                    "[update] last check %d hours ago, skipping",
                    self.options['interval'],
                )
                return

            display = agent.view()
            prev_status = display.get('status')
            display.update(force=True, new_data={'status': 'Checking for updates ...'})
            token = self.options.get('token', '') or ''

            # ----- check phase -----
            updates = []
            any_check_ok = False
            for target in self._targets():
                try:
                    info = check(target, token)
                    any_check_ok = True
                except Exception as e:
                    logging.warning("[update] %s: check failed: %s",
                                    target['name'], e)
                    continue
                if info.get('url'):
                    logging.warning(
                        "[update] %s: %s -> %s available (%s)",
                        info['name'], info['current'], info['available'],
                        info['url'],
                    )
                    updates.append(info)
                else:
                    logging.debug(
                        "[update] %s: up to date (%s)",
                        info['name'], info['current'],
                    )

            # Only mark "checked" if at least one repo's API call returned.
            # A pure-network failure should not throttle the next attempt
            # by `interval` hours.
            if any_check_ok:
                self.status.update()

            # ----- install phase -----
            if not self.options.get('install'):
                if updates:
                    display.update(force=True, new_data={
                        'status': '%d new update%s available!' % (
                            len(updates), 's' if len(updates) > 1 else '',
                        )
                    })
                    time.sleep(2)
                display.update(force=True, new_data={'status': prev_status or ''})
                return

            num_installed = 0
            for update in updates:
                installer = _INSTALLERS.get(update['kind'])
                if not installer:
                    logging.warning("[update] %s: unknown kind '%s'",
                                    update['name'], update['kind'])
                    continue
                try:
                    plugins.on('updating')
                    installer(update, display)
                    num_installed += 1
                    logging.info("[update] %s: installed %s",
                                 update['name'], update['available'])
                except Exception as e:
                    # Critical: never raise out of this loop. One target's
                    # install failure must not prevent the next target.
                    logging.error("[update] %s: install failed: %s",
                                  update['name'], e)
                    continue

            display.update(force=True, new_data={'status': prev_status or ''})

            if num_installed > 0:
                logging.info("[update] %d component(s) installed, rebooting",
                             num_installed)
                display.update(force=True, new_data={'status': 'Rebooting ...'})
                time.sleep(3)
                pwnagotchi.reboot()
