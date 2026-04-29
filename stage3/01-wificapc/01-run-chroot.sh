#!/bin/bash -e

WIFICAPC_TAG=v0.6.6

echo -e "\e[32m### Building and installing WiFiCapC ${WIFICAPC_TAG} ###\e[0m"
cd /tmp

# Retry the clone — chroot networks can blip and a one-shot failure
# shouldn't kill the whole image build.
n=0
until git clone --depth 1 --branch "${WIFICAPC_TAG}" \
        https://github.com/Asif-Iqbal-Gazi/WiFiCapC.git; do
  n=$((n+1))
  if [ $n -ge 3 ]; then
    echo "WiFiCapC: git clone failed after 3 attempts" >&2
    exit 1
  fi
  echo "WiFiCapC: clone attempt $n failed, retrying in 5s..." >&2
  sleep 5
done

cd WiFiCapC
make
make install   # binary only since v0.6.5; service comes from stage4 patches

# Surface install failures here instead of as "command not found" on first boot.
command -v wificapc >/dev/null 2>&1 || {
  echo "WiFiCapC: install completed but binary not found in PATH" >&2
  exit 1
}

cd /tmp
rm -rf WiFiCapC
