#!/bin/bash -e

cd /opt
if [ ! -d pwnagotchi ]; then
    git clone https://github.com/Asif-Iqbal-Gazi/pwnagotc.git pwnagotchi
    cd pwnagotchi/
else
    cd /opt/pwnagotchi/
    git pull
fi
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

ln -sf /opt/.pwn/bin/pwnagotchi /usr/bin/pwnagotchi
rm -r /opt/pwnagotchi
