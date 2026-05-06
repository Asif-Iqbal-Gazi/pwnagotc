#!/bin/bash -e

PWNAGOTCHI_TAG=v3.0.9

echo -e "\e[32m### Building and installing pwnagotchi ${PWNAGOTCHI_TAG} ###\e[0m"
cd /opt

# Retry the clone — chroot networks blip on us regularly. A one-shot
# git failure shouldn't kill the whole image build.
n=0
until git clone --depth 1 --branch "${PWNAGOTCHI_TAG}" \
        https://github.com/Asif-Iqbal-Gazi/pwnagotc.git pwnagotchi; do
  n=$((n+1))
  if [ $n -ge 3 ]; then
    echo "pwnagotchi: git clone failed after 3 attempts" >&2
    exit 1
  fi
  echo "pwnagotchi: clone attempt $n failed, retrying in 5s..." >&2
  rm -rf pwnagotchi
  sleep 5
done

cd pwnagotchi/

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

rm -r /opt/pwnagotchi
