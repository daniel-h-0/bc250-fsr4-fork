#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Isolated Vulkan loader/BC250 check using only Python's standard library."""

import argparse
import ctypes as C
import json
import os
from pathlib import Path


class ApplicationInfo(C.Structure):
    _fields_ = [
        ("sType", C.c_uint32),
        ("pNext", C.c_void_p),
        ("pApplicationName", C.c_char_p),
        ("applicationVersion", C.c_uint32),
        ("pEngineName", C.c_char_p),
        ("engineVersion", C.c_uint32),
        ("apiVersion", C.c_uint32),
    ]


class InstanceCreateInfo(C.Structure):
    _fields_ = [
        ("sType", C.c_uint32),
        ("pNext", C.c_void_p),
        ("flags", C.c_uint32),
        ("pApplicationInfo", C.POINTER(ApplicationInfo)),
        ("enabledLayerCount", C.c_uint32),
        ("ppEnabledLayerNames", C.c_void_p),
        ("enabledExtensionCount", C.c_uint32),
        ("ppEnabledExtensionNames", C.c_void_p),
    ]


class PropertiesHeader(C.Structure):
    # Vulkan 1.0's fixed prefix. Reserve aligned space for the opaque limits and
    # sparse-properties tail so this probe need not duplicate hundreds of fields.
    _fields_ = [
        ("apiVersion", C.c_uint32),
        ("driverVersion", C.c_uint32),
        ("vendorID", C.c_uint32),
        ("deviceID", C.c_uint32),
        ("deviceType", C.c_uint32),
        ("deviceName", C.c_char * 256),
    ]


def inspect(library, icd):
    for name in ("VK_ICD_FILENAMES", "VK_ADD_DRIVER_FILES"):
        os.environ.pop(name, None)
    os.environ["VK_DRIVER_FILES"] = str(icd)
    # Force all symbol relocations before asking the Vulkan loader to try it.
    try:
        C.CDLL(str(library), mode=os.RTLD_NOW | os.RTLD_LOCAL)
    except OSError as error:
        raise RuntimeError("Binary ABI/dependency check failed: " + str(error)) from error
    vk = C.CDLL("libvulkan.so.1", mode=os.RTLD_NOW | os.RTLD_LOCAL)
    vk.vkCreateInstance.argtypes = [
        C.POINTER(InstanceCreateInfo),
        C.c_void_p,
        C.POINTER(C.c_void_p),
    ]
    vk.vkCreateInstance.restype = C.c_int32
    vk.vkDestroyInstance.argtypes = [C.c_void_p, C.c_void_p]
    vk.vkDestroyInstance.restype = None
    vk.vkEnumeratePhysicalDevices.argtypes = [
        C.c_void_p,
        C.POINTER(C.c_uint32),
        C.POINTER(C.c_void_p),
    ]
    vk.vkEnumeratePhysicalDevices.restype = C.c_int32
    vk.vkGetPhysicalDeviceProperties.argtypes = [C.c_void_p, C.c_void_p]
    vk.vkGetPhysicalDeviceProperties.restype = None
    app = ApplicationInfo(sType=0, pApplicationName=b"BC250 FSR4 installer", apiVersion=1 << 22)
    create = InstanceCreateInfo(sType=1, pApplicationInfo=C.pointer(app))
    instance = C.c_void_p()
    result = vk.vkCreateInstance(C.byref(create), None, C.byref(instance))
    if result != 0:
        raise RuntimeError("vkCreateInstance failed: VkResult " + str(result))
    try:
        count = C.c_uint32(64)
        devices = (C.c_void_p * 64)()
        result = vk.vkEnumeratePhysicalDevices(instance, C.byref(count), devices)
        if result != 0:
            raise RuntimeError("vkEnumeratePhysicalDevices failed: VkResult " + str(result))
        found = []
        for device in devices[: count.value]:
            data = (C.c_uint64 * 512)()
            vk.vkGetPhysicalDeviceProperties(device, data)
            info = PropertiesHeader.from_buffer(data)
            found.append(
                {
                    "vendor_id": hex(info.vendorID),
                    "device_id": hex(info.deviceID),
                    "name": info.deviceName.decode("utf-8", errors="replace"),
                }
            )
        if not any(
            d["vendor_id"] == "0x1002"
            and d["device_id"] == "0x13fe"
            and "radv" in d["name"].lower()
            and "gfx1013" in d["name"].lower()
            for d in found
        ):
            raise RuntimeError(
                "Selected driver did not expose BC250 RADV GFX1013: " + json.dumps(found)
            )
        return {
            "success": True,
            "loader": "libvulkan.so.1",
            "relocations": "RTLD_NOW",
            "devices": found,
        }
    finally:
        vk.vkDestroyInstance(instance, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--icd", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.library, args.icd)))
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"success": False, "error": str(error)}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
