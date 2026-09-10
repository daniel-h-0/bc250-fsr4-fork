# SPDX-License-Identifier: MIT
"""Derivation helper: lower only two-lane 16-to-32-bit integer extensions.

The checked-in shader assembly already contains this transformation. This
helper documents and tests the RC7 compatibility change; it is not run by
the ordinary reproducible builder.
"""

import re

CAST = re.compile(r"  (%\w+) = (sext|zext) <2 x i16> (%\w+) to <2 x i32>")


def lower(text):
    if "%compatcast" in text:
        raise ValueError("Input already uses the reserved compatibility temporary names")
    result = []
    count = 0
    for line in text.splitlines():
        match = CAST.fullmatch(line)
        if not match:
            result.append(line)
            continue
        dest, op, source = match.groups()
        count += 1
        temp = "%compatcast" + str(count)
        for lane in range(2):
            result.append(f"  {temp}n{lane} = extractelement <2 x i16> {source}, i32 {lane}")
            result.append(f"  {temp}w{lane} = {op} i16 {temp}n{lane} to i32")
        result.append(f"  {temp}v = insertelement <2 x i32> undef, i32 {temp}w0, i32 0")
        result.append(f"  {dest} = insertelement <2 x i32> {temp}v, i32 {temp}w1, i32 1")
    return "\n".join(result) + "\n", count
