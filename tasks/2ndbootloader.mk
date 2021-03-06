ifdef BOARD_BLOBSTORE_CONFIG

build_blobstore := device/intel/build/build_blobstore.py
blobstore_deps := $(build_blobstore) device/intel/build/blobstore.py $(BOARD_BLOBSTORE_CONFIG)
ifdef BOARD_DEVICE_MAPPING
blobstore_deps += $(BOARD_DEVICE_MAPPING)
blobstore_extra_args := --device-map $(BOARD_DEVICE_MAPPING)
endif

# build_blobstore without an output parameter lists all the necessary
# source blob files we need
blobstore_deps += $(shell $(build_blobstore) \
			--config $(BOARD_BLOBSTORE_CONFIG) \
			$(blobstore_extra_args))

$(INSTALLED_2NDBOOTLOADER_TARGET): $(blobstore_deps)
	$(build_blobstore) --config $(BOARD_BLOBSTORE_CONFIG) \
			$(blobstore_extra_args) --output $@
else ifdef BOARD_DTB_FILE
# Non-scalable SoFIA targets

LOCAL_DTB_PATH := $(LOCAL_KERNEL_PATH)/$(BOARD_DTB_FILE)

$(INSTALLED_2NDBOOTLOADER_TARGET): $(LOCAL_DTB_PATH) | $(ACP)
	$(hide) $(ACP) -fp $(LOCAL_DTB_PATH) $@

else ifdef BOARD_OEM_VARS
# Non-scalable EFI targets that use oemvars

$(INSTALLED_2NDBOOTLOADER_TARGET): $(BOARD_OEM_VARS)
	$(hide) echo "#OEMVARS" > $@
	$(hide) cat $(BOARD_OEM_VARS) >> $@

endif
