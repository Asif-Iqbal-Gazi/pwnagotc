#!/bin/bash -e

echo -e "\e[32m### Creating Pwnagotchi folders ###\e[0m"
install -v -d "${ROOTFS_DIR}/etc/pwnagotchi"
install -v -d "${ROOTFS_DIR}/etc/pwnagotchi/log"
install -v -d "${ROOTFS_DIR}/etc/pwnagotchi/conf.d/"
install -v -d "${ROOTFS_DIR}/etc/pwnagotchi/custom-plugins/"
install -v -d "${ROOTFS_DIR}/etc/pwnagotchi/handshakes/"
install -v -d "${ROOTFS_DIR}/etc/pwnagotchi/backups/"
install -v -d "${ROOTFS_DIR}/etc/pwnagotchi/sessions/"

# Stage the pwnagotchi source *we are building* (this exact checkout) into the
# image so the chroot installs it directly — no separate PWNAGOTCHI_TAG re-clone
# that can go stale and silently ship an old agent in a new image.
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${ROOTFS_DIR}/opt/pwnagotchi-src"
echo -e "\e[32m### Staging pwnagotchi source from ${SRC_ROOT} ###\e[0m"
rm -rf "${DEST}"
mkdir -p "${DEST}"
rsync -a \
  --exclude '.git' --exclude 'pi-gen-64bit' --exclude 'stage3' \
  --exclude 'work-64bit' --exclude 'hs' --exclude 'wifi' \
  --exclude '*.img' --exclude '*.img.xz' \
  --exclude '__pycache__' --exclude '*.egg-info' \
  "${SRC_ROOT}/" "${DEST}/"
if [ ! -f "${DEST}/pwnagotchi/_version.py" ]; then
  echo "pwnagotchi: staged source missing _version.py at ${DEST}" >&2
  exit 1
fi