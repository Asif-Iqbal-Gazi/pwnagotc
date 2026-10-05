#!/bin/bash -e

# Install pwnagotchi from the source staged by 00-run.sh (the exact checkout the
# image is built from), NOT a re-cloned tag. This removes the old PWNAGOTCHI_TAG
# footgun where a stale pin shipped an old agent inside a fresh image.
SRC=/opt/pwnagotchi-src

if [ ! -f "${SRC}/pwnagotchi/_version.py" ]; then
  echo "pwnagotchi: staged source not found at ${SRC}" >&2
  exit 1
fi
VER="$(cut -d"'" -f2 < "${SRC}/pwnagotchi/_version.py")"
echo -e "\e[32m### Installing pwnagotchi ${VER} from ${SRC} ###\e[0m"
cd "${SRC}"

if [ -d /opt/.pwn ]; then
    rm -r /opt/.pwn
fi
if [ "$(uname -m)" = "armv6l" ]; then
    export QEMU_CPU=arm1176
fi

echo -e "\e[32m### Installing python virtual environment ###\e[0m"
python3 -m venv /opt/.pwn/ --system-site-packages
echo -e "\e[32m### Activating virtual environment ###\e[0m"
source /opt/.pwn/bin/activate

echo -e "\e[32m### Installing Pwnagotchi ###\e[0m"
pip3 cache purge
pip3 install . --no-cache-dir
deactivate

# Surface install failures here instead of as "command not found" on
# first boot.
ln -sf /opt/.pwn/bin/pwnagotchi /usr/bin/pwnagotchi
command -v pwnagotchi >/dev/null 2>&1 || {
  echo "pwnagotchi: install completed but binary not found in PATH" >&2
  exit 1
}

rm -rf /opt/pwnagotchi-src
