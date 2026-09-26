# Set an absolute path in the config file for WORK_DIR and DEPLOY_DIR
# DEPLOY_DIR is where the final image will be stored
# WORK_DIR is where all the data is stored before merged into an image
# WORK_DIR can use up to 20GB of storage space
# refer to https://github.com/RPi-Distro/pi-gen/blob/master/README.md
# sudo apt-get install -y make git quilt qemu-user-static debootstrap zerofree libarchive-tools curl pigz arch-test qemu-utils qemu-system-arm qemu-user
# gcc-aarch64-linux-gnu gcc-arm-linux-gnueabihf
# Building the nexmon DKMS package additionally needs:
# sudo apt-get install -y debhelper dh-sequence-dkms dpkg-dev

BUILD_USER ?= $(shell whoami)
BUILD_HOME ?= $(shell eval echo ~$(BUILD_USER))
IMAGE_DIR ?= $(BUILD_HOME)/images

NEXMON_DKMS_DIR := stage3/02-nexmon/brcmfmac-nexmon-dkms
NEXMON_DKMS_OUT := stage3/02-nexmon/files
NEXMON_DRIVER_SRC := stage3/02-nexmon/nexmon/patches/driver/brcmfmac_6.18.y-nexmon

.PHONY: submodules update-submodules nexmon-dkms 32bit 64bit update_langs compile_langs

# Check out every submodule at the commit this tree pins. Run after a fresh
# clone — the image build needs the nexmon firmware + driver trees.
submodules:
	git submodule update --init --recursive

# Fast-forward each submodule to the tip of the branch .gitmodules tracks.
# Review 'git diff' and test-build before committing the pointer bumps.
update-submodules:
	git submodule update --init --recursive --remote
	@echo "submodule pointers moved - review 'git diff' and test-build before committing"

# Build the brcmfmac-nexmon DKMS package from the submodule. The nexmon repo
# is canonical for the driver .c/.h; copy those over the packaging tree
# (debian/ + dkms.conf + Makefile + Kconfig) so the two cannot drift, then
# build the .deb. DKMS compiles brcmfmac.ko per-kernel at install time.
nexmon-dkms:
	@command -v dh_dkms >/dev/null || { echo "dh-sequence-dkms is not installed (see the header of this Makefile)"; exit 1; }
	[ -f $(NEXMON_DKMS_DIR)/debian/changelog ] || { echo "$(NEXMON_DKMS_DIR) is empty - run 'make submodules' first"; exit 1; }
	[ -f $(NEXMON_DRIVER_SRC)/sdio.c ] || { echo "$(NEXMON_DRIVER_SRC) is missing - run 'make submodules' first"; exit 1; }
	cp -v $(NEXMON_DRIVER_SRC)/*.c $(NEXMON_DRIVER_SRC)/*.h $(NEXMON_DKMS_DIR)/
	cd $(NEXMON_DKMS_DIR) && dpkg-buildpackage -us -uc -b
	mkdir -p $(NEXMON_DKMS_OUT)
	rm -f $(NEXMON_DKMS_OUT)/brcmfmac-nexmon-dkms_*.deb
	mv stage3/02-nexmon/brcmfmac-nexmon-dkms_*_all.deb $(NEXMON_DKMS_OUT)/
	rm -f stage3/02-nexmon/brcmfmac-nexmon-dkms_*.buildinfo stage3/02-nexmon/brcmfmac-nexmon-dkms_*.changes
	cd $(NEXMON_DKMS_DIR) && dh_clean && git checkout -- dkms.conf
	@echo "built $$(ls $(NEXMON_DKMS_OUT)/brcmfmac-nexmon-dkms_*.deb)"

# clone pi-gen into pi-gen-32bit folder
32bit: nexmon-dkms
	[ -d pi-gen-32bit ] || git clone "https://github.com/RPi-Distro/pi-gen.git" pi-gen-32bit
	[ -d pi-gen-32bit ] && cd pi-gen-32bit && git pull
	rm -rf pi-gen-32bit/stage2/EXPORT_IMAGE
	sed -i "s|WORK_DIR=.*|WORK_DIR=\"$(BUILD_HOME)/work-32bit\"|" config-32bit
	sed -i "s|DEPLOY_DIR=.*|DEPLOY_DIR=\"$(IMAGE_DIR)\"|" config-32bit
	sudo ./pi-gen-32bit/build.sh -c config-32bit
	mkdir -p $(IMAGE_DIR)
	sudo chown $(BUILD_USER):$(BUILD_USER) -R $(IMAGE_DIR)

# clone pi-gen arm64 branch into pi-gen-64bit folder
64bit: nexmon-dkms
	[ -d pi-gen-64bit ] || git clone --branch arm64 "https://github.com/RPI-Distro/pi-gen.git" pi-gen-64bit
	[ -d pi-gen-64bit ] && cd pi-gen-64bit && git pull
	rm -rf pi-gen-64bit/stage2/EXPORT_IMAGE
	sed -i "s|WORK_DIR=.*|WORK_DIR=\"$(BUILD_HOME)/work-64bit\"|" config-64bit
	sed -i "s|DEPLOY_DIR=.*|DEPLOY_DIR=\"$(IMAGE_DIR)\"|" config-64bit
	sudo ./pi-gen-64bit/build.sh -c config-64bit
	mkdir -p $(IMAGE_DIR)
	sudo chown $(BUILD_USER):$(BUILD_USER) -R $(IMAGE_DIR)

update_langs:
	@for lang in pwnagotchi/locale/*/; do\
		echo "updating language: $$lang ..."; \
		./scripts/language.sh update $$(basename $$lang); \
	done

compile_langs:
	@for lang in pwnagotchi/locale/*/; do\
		echo "compiling language: $$lang ..."; \
		./scripts/language.sh compile $$(basename $$lang); \
	done
