#!/usr/bin/python
"""
This script is used to create a blobstore archive at build time which can later
be processed by the bootloader. A special JSON configuration file is used to
create this blobstore.

The "meta" block in the configuration file specifies a few useful things:
    * "version" indicating what version of this script the configuration
      was generated against. So far we just have Version 1. Increment this
      if changes are made to the JSON format which break older configuration
      files.
    * "base_dir" This is root path used to compute individual paths to
      the blob data to be included in the blobstore. The specific way this
      base path is used depends on how the key values are determined, as
      described below.
    * "types" enumerates the specific blob types we're going to put inside
      this blobstore. It maps specific type names (currently "oemvars", "dtb",
      "bootvars") to the filename on the disk to look for, as explained in more
      detail below. We call this the "type filename".

We need to determine the key values to store the blob data, such that the keys
match up with device identification information provided by the bootloader.
The set of keys used can be specified in a few different ways:

    * If the --device-map option is provided on the command line, the data will
      be pulled from there. This is for EFI devices which pull build fingerprint
      information out of SMBIOS. The key used in the blobstore is structured as
      "<brand>/<product>/<device>" since we have found that we can only
      uniquely identify a board with all three of these values. For the <device>
      value, we strip out the "fish name" suffix since that can't be detected
      from DMI.

      For each board in the device_map, we use the variant name in the path.
      The path to look for a particular blob is
      <base_dir>/<variant>/<type filename>

      As an example, for ECS2-8A device, we would map in the blobstore for
      oemvars as follows:

      "meta": {
          "version" : 1,
          "base_dir" : "device/intel/coho/flashfiles",
          "types" : {
              "oemvars" : "oemvars.txt"
          }
       }

       So for ECS28-A we'd map "ecs/ecs28a/ecs28a_0" to the file in
       device/intel/coho/flashfiles/ECS28A/oemvars.txt

    * If --device-map is not used, then we look for a "devices" block in
      the configuration file. This is a list of device names, for example:

      "devices" : ["sofia3gr-foo", "sofia3gr-bar", "sofia3gr-baz"]

      Assuming the same "meta" block in the --device-map example was used,
      It will then map, for the above devices, the following files respectively:

      device/intel/coho/flashfiles/sofia3gr-foo/oemvars.txt
      device/intel/coho/flashfiles/sofia3gr-bar/oemvars.txt
      device/intel/coho/flashfiles/sofia3gr-baz/oemvars.txt

If for any reason a file can't be found, a warning message is printed but
the blobstore is still assembled. This is to support scenarios where not all
boards require blob data of a specific type.
"""

import blobstore
import os
import sys, getopt
import json
import argparse
sys.path.append("device/intel/build/releasetools")
import intel_common

btypes = {
    "oemvars" : blobstore.BLOB_TYPE_OEMVARS,
    "dtb" : blobstore.BLOB_TYPE_DTB,
    "bootvars" : blobstore.BLOB_TYPE_BOOTVARS
    }

def main(argv):
    args = None
    configData = None
    configKeys = None
    #verify options
    try:
        parser = argparse.ArgumentParser(epilog=__doc__,
                formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument('--config',required=True,
                        help="blob configuration file")
        parser.add_argument('--output',required=False,
                        help="blobstore output file path. If omitted, just list dependencies")
        parser.add_argument("--device-map",required=False,
                        help="device mapping database")
        args = parser.parse_args()
    except argparse.ArgumentError:
        sys.exit(1)

    in_file = open(args.config, 'r')
    configData = json.load(in_file)
    in_file.close()

    metadata = configData["meta"]
    version = metadata["version"]
    if version != 1:
        sys.stderr.write("Error: Unsupported configuration file version\n")
        sys.exit(2)

    if args.device_map:
        dmap = intel_common.load_device_mapping(args.device_map)
        if not dmap:
            sys.stderr.write("Device map unavailable\n")
            sys.exit(2)
    else:
        dmap = None

    configTypes = len(metadata["types"].keys())

    blobs = {}
    if dmap:
        # dmap for EFI devices, we just build a fingerprint minus the trailing
        # "fish name" since we can pull all that out of DMI. The "fish name"
        # is a code name that can't be derived at runtime, it gets added to
        # the build fingerprint later by init's autodetect.c using a hardcoded
        # value
        for k in dmap.keys():
            if k.startswith("__"):
                continue

            # Assumes version 1
            brand, product, device, lunch, fish, basev = dmap[k]
            # Nip off trailing _coho since that is not in DMI
            device = device[:-(len(fish) + 1)]

            device_id = "%s/%s/%s" % (brand, product, device)
            for t, fn in metadata["types"].iteritems():
                path = os.path.join(metadata["base_dir"], k, fn)
                blobs[(device_id, btypes[t])] = path
    elif "devices" in configData:
        # Non-EFI, just have a list of device ids as reported by
        # respective loaders. We assume here that these ids can
        # be used in a UNIX path
        for device_id in configData["devices"]:
            for t, fn in metadata["types"].iteritems():
                path = os.path.join(metadata["base_dir"], device_id, fn)
                blobs[(device_id, btypes[t])] = path
    else:
        sys.stderr.write("No device mapping or 'devices' in JSON configuration")
        sys.exit(2)

    if not args.output:
        for v in set(blobs.values()):
            if os.path.exists(v):
                print v,
        sys.exit(0)

    #populate datastore
    db = blobstore.BlobStore(args.output)
    for k, v in blobs.iteritems():
        device_id, blobtype = k
        if not os.path.exists(v):
            sys.stderr.write(v + " doesn't exist, skipping\n");
            continue

        db.add(device_id, blobtype, v)
    db.commit()

if __name__ == '__main__':
    main(sys.argv[1:])
    sys.exit(0)
