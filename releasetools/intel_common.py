
#
# Copyright (C) 2014 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import tempfile
import os
import sys
import subprocess
import shlex
import shutil
import imp

sys.path.append("build/tools/releasetools")
import common


def load_device_mapping(path):
    try:
        mod = imp.load_module("device_mapping", open(path, "U"), path,
                              (".py", "U", imp.PY_SOURCE))
    except ImportError:
        print "Device mapping not found"
        return None

    return mod.dmap


def load_device_mapping_from_tfp(tfp_path):
    return load_device_mapping(os.path.join(tfp_path, "RADIO",
                                            "device_mapping.py"))


def der_pub_from_pem_cert(cert_path):
    tf = tempfile.NamedTemporaryFile(prefix="der_pub_from_pem_cert")

    cmd1 = ["openssl", "x509",
            "-in", cert_path,
            "-noout", "-pubkey"]
    cmd2 = ["openssl", "rsa",
            "-inform", "PEM",
            "-pubin",
            "-outform", "DER",
            "-out", tf.name]
    p1 = common.Run(cmd1, stdout=subprocess.PIPE)
    p2 = common.Run(cmd2, stdin=p1.stdout)
    p2.communicate()
    p1.wait()
    assert p1.returncode == 0, "extracting verity public key failed"
    assert p2.returncode == 0, "verity public key conversion failed"

    tf.seek(os.SEEK_SET, 0)
    return tf


def pem_cert_to_der_cert(pem_cert_path):
    tf = tempfile.NamedTemporaryFile(prefix="pem_cert_to_der_cert")

    cmd = ["openssl", "x509", "-inform", "PEM", "-outform", "DER",
        "-in", pem_cert_path, "-out", tf.name]
    p = common.Run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    p.communicate()
    assert p.returncode == 0, "openssl cert conversion failed"
    tf.seek(os.SEEK_SET, 0)
    return tf


def pk8_to_pem(der_key_path, password=None, none_on_fail_convert=False):
    # If the key is already available in converted form, then use that
    # file. This is important for .pk8 files that actually contain references
    # to ECSS keys, because they are not fully parseable by openssl.
    (der_key_path_root,der_key_path_ext) = os.path.splitext(der_key_path)
    der_key_path_pem = der_key_path_root + ".pem"
    if os.path.exists(der_key_path_pem):
        return open(der_key_path_pem)

    # Defaults to 0600 permissions which is defintitely what we want!
    tf = tempfile.NamedTemporaryFile(prefix="pk8_to_pem")

    cmd = ["openssl", "pkcs8"];
    if password:
        cmd.extend(["-passin", "stdin"])
    else:
        cmd.append("-nocrypt")

    cmd.extend(["-inform", "DER", "-outform", "PEM",
        "-in", der_key_path, "-out", tf.name])
    p = common.Run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    if password is not None:
        password += "\n"
    p.communicate(password)
    if none_on_fail_convert:
        if p.returncode != 0:
            tf.close()
            return None
    else:
        assert p.returncode == 0, "openssl key conversion failed"
    tf.seek(os.SEEK_SET, 0)
    return tf


def WriteFileToDest(img, dest):
    """Write common.File to destination"""
    fid = open(dest, 'w')
    fid.write(img.data)
    fid.flush()
    fid.close()


def patch_or_verbatim_exists(path, ota_dir):
    filepath = os.path.join(ota_dir, "bootloader", path)
    patchpath = os.path.join(ota_dir, "patch", "bootloader", path + ".p")
    return os.path.exists(filepath) or os.path.exists(patchpath)


def ComputeBootloaderPatch(source_tfp_dir, target_tfp_dir, variant=None,
                           base_variant=None, existing_ota_dir=None):
    target_data = LoadBootloaderFiles(target_tfp_dir, variant=variant, base_variant=base_variant)
    source_data = LoadBootloaderFiles(source_tfp_dir, variant=variant, base_variant=base_variant)

    diffs = []

    # List of files that will be included in the OTA verbatim because
    # they are either new or the patch is > 95% in size of the original
    # file. If this isn't empty you just need to call edify generator
    # UnpackPackageDir("bootloader", "/bootloader")
    verbatim_targets = []

    # Returned list of common.File objects that need to be added to
    # the OTA archive, for each one call AddToZip()
    output_files = []

    # Returned list of patches to be created.
    # Each element is a tuple of the form (path, target File object,
    # source File object, target file size)
    patch_list = []

    for fn in sorted(target_data.keys()):
        if existing_ota_dir and patch_or_verbatim_exists(fn, existing_ota_dir):
            continue

        tf = target_data[fn]
        sf = source_data.get(fn, None)

        if sf is None:
            verbatim_targets.append(fn)
            output_files.append(tf)
        elif tf.sha1 != sf.sha1:
            diffs.append(common.Difference(tf, sf))

    common.ComputeDifferences(diffs)

    for diff in diffs:
        tf, sf, d = diff.GetPatch()
        if d is None or len(d) > tf.size * 0.95:
            output_files.append(tf)
            verbatim_targets.append(tf.name)
        else:
            output_files.append(common.File("patch/" + tf.name + ".p", d))
            patch_list.append((tf, sf))

    # output list of files that need to be deleted, pass this to
    # edify generator DeleteFiles in InstallEnd
    delete_files = ["/bootloader/"+i for i in sorted(source_data) if i not in target_data]

    return (output_files, delete_files, patch_list, verbatim_targets)


def LoadBootloaderFiles(tfpdir, extra_files=None, variant=None, base_variant=None):
    out = {}
    data = GetBootloaderImageFromTFP(tfpdir, extra_files=extra_files,
                                     variant=variant, base_variant=base_variant)
    image = common.File("bootloader.img", data).WriteToTemp()

    # Extract the contents of the VFAT bootloader image so we
    # can compute diffs on a per-file basis
    esp_root = tempfile.mkdtemp(prefix="bootloader-")
    common.OPTIONS.tempfiles.append(esp_root)
    add_dir_to_path("/sbin")
    subprocess.check_output(["mcopy", "-s", "-i", image.name, "::*", esp_root]);
    image.close();

    for dpath, dname, fnames in os.walk(esp_root):
        for fname in fnames:
            # Capsule update file -- gets consumed and deleted by the firmware
            # at first boot, shouldn't try to patch it
            if (fname == "BIOSUPDATE.fv"):
                continue
            abspath = os.path.join(dpath, fname)
            relpath = os.path.relpath(abspath, esp_root)
            data = open(abspath).read()
            out[relpath] = common.File("bootloader/" + relpath, data)

    return out


def GetBootloaderImageFromTFP(unpack_dir, autosize=False, extra_files=None, variant=None, base_variant=None):
    if extra_files == None:
        extra_files = []

    if variant:
        provdata_name = os.path.join(unpack_dir, "RADIO", "provdata_" + variant +".zip")
        if base_variant and (os.path.isfile(provdata_name) == False):
            provdata_name = os.path.join(unpack_dir, "RADIO", "provdata_" + base_variant +".zip")
        provdata, provdata_zip = common.UnzipTemp(provdata_name)
        cap_path = os.path.join(provdata,"capsule.fv")
        if os.path.exists(cap_path):
            extra_files.append((cap_path, "capsules/current.fv"))
            extra_files.append((cap_path, "BIOSUPDATE.fv"))
        else:
            print "No capsule.fv found in provdata_" + variant + ".zip"
        base_bootloader = os.path.join(provdata, "BOOTLOADER")
        if os.path.exists(base_bootloader):
            for root, dirs, files in os.walk(base_bootloader):
                for name in files:
                    fullpath = os.path.join(root, name)
                    relpath = os.path.relpath(fullpath, base_bootloader)
                    print "Adding extra bootloader file", relpath
                    extra_files.append((fullpath, relpath))

    bootloader = tempfile.NamedTemporaryFile(delete=False)
    filename = bootloader.name
    bootloader.close()

    fastboot = GetFastbootImage(unpack_dir)
    if fastboot:
        fastboot_file = fastboot.WriteToTemp()
        extra_files.append((fastboot_file.name,"fastboot.img"))

    tdos = GetTdosImage(unpack_dir)
    if tdos:
        tdos_file = tdos.WriteToTemp()
        extra_files.append((tdos_file.name,"tdos.img"))

    if not autosize:
        size = int(open(os.path.join(unpack_dir, "RADIO", "bootloader-size.txt")).read().strip())
    else:
        size = 0
    MakeVFATFilesystem(os.path.join(unpack_dir, "RADIO", "bootloader.zip"),
            filename, size=size, extra_files=extra_files)
    bootloader = open(filename)
    data = bootloader.read()
    bootloader.close()
    os.unlink(filename)
    return data


def MakeVFATFilesystem(root_zip, filename, title="ANDROIDIA", size=0, extra_size=0,
        extra_files=[]):
    """Create a VFAT filesystem image with all the files in the provided
    root zipfile. The size of the filesystem, if not provided by the
    caller, will be 101% the size of the containing files"""

    root, root_zip = common.UnzipTemp(root_zip)
    for fn_src, fn_dest in extra_files:
        fn_dest = os.path.join(root, fn_dest)
        if not os.path.exists(os.path.dirname(fn_dest)):
            os.makedirs(os.path.dirname(fn_dest))
        shutil.copy(fn_src, fn_dest)

    if size == 0:
        for dpath, dnames, fnames in os.walk(root):
            for f in fnames:
                size += os.path.getsize(os.path.join(dpath, f))

        # Add 1% extra space, minimum 32K
        extra = size / 100
        if extra < (32 * 1024):
            extra = 32 * 1024
        size += extra

    size += extra_size

    # Round the size of the disk up to 32K so that total sectors is
    # a multiple of sectors per track (mtools complains otherwise)
    mod = size % (32 * 1024)
    if mod != 0:
        size = size + (32 * 1024) - mod

    # mtools freaks out otherwise
    if os.path.exists(filename):
        os.unlink(filename)

    add_dir_to_path("/sbin")
    cmd = ["mkdosfs", "-n", title, "-C", filename, str(size / 1024)]
    try:
        p = common.Run(cmd)
    except Exception as exc:
        print "Error: Unable to execute command: {}".format(' '.join(cmd))
        raise exc
    p.wait()
    assert p.returncode == 0, "mkdosfs failed"
    for f in os.listdir(root):
        in_p = os.path.join(root, f)
        out_p = os.path.relpath(in_p, root)
        PutFatFile(filename, in_p, out_p)


def GetTdosImage(unpack_dir, info_dict=None):
    if info_dict is None:
        info_dict = common.OPTIONS.info_dict

    prebuilt_path = os.path.join(unpack_dir, "RADIO", "tdos.img")
    if (os.path.exists(prebuilt_path)):
        print "using prebuilt tdos.img"
        return common.File.FromLocalFile("tdos.img", prebuilt_path)

    ramdisk_path = os.path.join(unpack_dir, "RADIO", "ramdisk-tdos.img")
    if not os.path.exists(ramdisk_path):
        print "no TDOS ramdisk found"
        return None

    print "building TDOS image from target_files..."
    ramdisk_img = tempfile.NamedTemporaryFile()
    img = tempfile.NamedTemporaryFile()

    # use MKBOOTIMG from environ, or "mkbootimg" if empty or not set
    mkbootimg = os.getenv('MKBOOTIMG') or "mkbootimg"

    cmd = [mkbootimg, "--kernel", os.path.join(unpack_dir, "BOOT", "kernel")]
    fn = os.path.join(unpack_dir, "BOOT", "cmdline")
    if os.access(fn, os.F_OK):
        cmd.append("--cmdline")
        cmd.append(open(fn).read().rstrip("\n"))

    # Add 2nd-stage loader, if it exists
    fn = os.path.join(unpack_dir, "BOOT", "second")
    if os.access(fn, os.F_OK):
        cmd.append("--second")
        cmd.append(fn)

    args = info_dict.get("mkbootimg_args", None)
    if args and args.strip():
        cmd.extend(shlex.split(args))

    cmd.extend(["--ramdisk", ramdisk_path,
                "--output", img.name])

    try:
        p = common.Run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    except Exception as exc:
        print "Error: Unable to execute command: {}".format(' '.join(cmd))
        raise exc
    p.communicate()
    assert p.returncode == 0, "mkbootimg of fastboot image failed"

    # Sign the image using BOOT_SIGNER env variable, or "boot_signer" command
    signing_key = info_dict.get("verity_key")
    if info_dict.get("verity") == "true" and signing_key:
            boot_signer = os.getenv('BOOT_SIGNER') or "boot_signer"
            cmd = [boot_signer, "/tdos", img.name,
                    signing_key + common.OPTIONS.private_key_suffix,
                    signing_key + common.OPTIONS.public_key_suffix, img.name];
            try:
                p = common.Run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            except Exception as exc:
                    print "Error: Unable to execute command: {}".format(' '.join(cmd))
                    raise exc
            p.communicate()
            assert p.returncode == 0, "boot signing of tdos image failed"

    img.seek(os.SEEK_SET, 0)
    data = img.read()

    img.close()

    return common.File("tdos.img", data)


def GetFastbootImage(unpack_dir, info_dict=None):
    """Return a File object 'fastboot.img' with the Fastboot boot image.
    It will either be fetched from RADIO/fastboot.img or built
    using RADIO/ufb_ramdisk.zip, RADIO/ufb_cmdline, and BOOT/kernel"""

    if info_dict is None:
        info_dict = common.OPTIONS.info_dict

    prebuilt_path = os.path.join(unpack_dir, "RADIO", "fastboot.img")
    if (os.path.exists(prebuilt_path)):
        print "using prebuilt fastboot.img"
        return common.File.FromLocalFile("fastboot.img", prebuilt_path)

    ramdisk_path = os.path.join(unpack_dir, "RADIO", "ufb-ramdisk.zip")
    if not os.path.exists(ramdisk_path):
        print "no user fastboot image found, assuming efi fastboot"
        return None

    print "building Fastboot image from target_files..."
    ramdisk_img = tempfile.NamedTemporaryFile()
    img = tempfile.NamedTemporaryFile()

    ramdisk_tmp, ramdisk_zip = common.UnzipTemp(ramdisk_path)

    cmd1 = ["mkbootfs", ramdisk_tmp]
    try:
        p1 = common.Run(cmd1, stdout=subprocess.PIPE)
    except Exception as exc:
        print "Error: Unable to execute command: {}".format(' '.join(cmd1))
        shutil.rmtree(ramdisk_tmp)
        raise exc

    cmd2 = ["minigzip"]
    try:
        p2 = common.Run(
            cmd2, stdin=p1.stdout, stdout=ramdisk_img.file.fileno())
    except Exception as exc:
        print "Error: Unable to execute command: {}".format(' '.join(cmd2))
        shutil.rmtree(ramdisk_tmp)
        raise exc

    p1.stdout.close()
    p2.communicate()
    p1.wait()
    assert p1.returncode == 0, "mkbootfs of fastboot ramdisk failed"
    assert p2.returncode == 0, "minigzip of fastboot ramdisk failed"

    # use MKBOOTIMG from environ, or "mkbootimg" if empty or not set
    mkbootimg = os.getenv('MKBOOTIMG') or "mkbootimg"

    cmd = [mkbootimg, "--kernel", os.path.join(unpack_dir, "BOOT", "kernel")]
    fn = os.path.join(unpack_dir, "RADIO", "ufb-cmdline")
    if os.access(fn, os.F_OK):
        cmd.append("--cmdline")
        cmd.append(open(fn).read().rstrip("\n"))

    # Add 2nd-stage loader, if it exists
    fn = os.path.join(unpack_dir, "RADIO", "ufb-second")
    if os.access(fn, os.F_OK):
        cmd.append("--second")
        cmd.append(fn)

    args = info_dict.get("mkbootimg_args", None)
    if args and args.strip():
        cmd.extend(shlex.split(args))

    cmd.extend(["--ramdisk", ramdisk_img.name,
                "--output", img.name])

    try:
        p = common.Run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    except Exception as exc:
        print "Error: Unable to execute command: {}".format(' '.join(cmd))
        raise exc
    p.communicate()
    assert p.returncode == 0, "mkbootimg of fastboot image failed"

    # Sign the image using BOOT_SIGNER env variable, or "boot_signer" command
    signing_key = info_dict.get("verity_key")
    if info_dict.get("verity") == "true" and signing_key:
            boot_signer = os.getenv('BOOT_SIGNER') or "boot_signer"
            cmd = [boot_signer, "/fastboot", img.name,
                    signing_key + common.OPTIONS.private_key_suffix,
                    signing_key + common.OPTIONS.public_key_suffix, img.name];
            try:
                p = common.Run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            except Exception as exc:
                    print "Error: Unable to execute command: {}".format(' '.join(cmd))
                    raise exc
            p.communicate()
            assert p.returncode == 0, "boot signing of fastboot image failed"

    img.seek(os.SEEK_SET, 0)
    data = img.read()

    ramdisk_img.close()
    img.close()

    return common.File("fastboot.img", data)


def PutFatFile(fat_img, in_path, out_path):
    cmd = ["mcopy", "-s", "-Q", "-i", fat_img, in_path,
           "::" + out_path]
    try:
        p = common.Run(cmd)
    except Exception as exc:
        print "Error: Unable to execute command: {}".format(' '.join(cmd))
        raise exc
    p.wait()
    assert p.returncode == 0, "couldn't insert %s into FAT image" % (in_path,)


def add_dir_to_path(dir_name, end=True):
    """
    I add a directory to the PATH environment variable, if not already in the
    path.  By default it gets added to the end of the PATH
    """
    dir_name = os.path.abspath(dir_name)
    path_env_var = os.environ.get('PATH', "")
    for path_dir in path_env_var.split(os.pathsep):
        path_dir = os.path.abspath(path_dir)
        if dir_name == path_dir:
            return
    if end:
        path_env_var += ":" + dir_name
    else:
        path_env_var = dir_name + ":" + path_env_var
    os.environ['PATH'] = path_env_var

