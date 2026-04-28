#!/bin/bash -e

echo -e "\e[32m### Building and installing WiFiCapC ###\e[0m"
cd /tmp
git clone https://github.com/Asif-Iqbal-Gazi/WiFiCapC.git
cd WiFiCapC
make
make install
cd /tmp
rm -rf WiFiCapC
