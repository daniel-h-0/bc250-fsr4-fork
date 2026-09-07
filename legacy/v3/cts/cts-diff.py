#!/usr/bin/env python3
"""Differential Vulkan CTS comparator.

Compares the per-case verdicts of two deqp-vk .qpa logs (stock vs patched
RADV driver) to prove the FSR4 patch introduced no behavioural regression.

.QA format is line-oriented text:
    #beginTestCaseResult <path>
    <TestCaseResult CasePath="..." ...><Result StatusCode="Pass">...</Result>
    #endTestCaseResult

Usage: cts-diff.py <stock.qpa> <patched.qpa>
Exit codes: 0 clean (no differences), 1 regression, 2 changed non-pass,
3 RAN-ONLY coverage gap, 4 parse problem.
"""

import re
import sys

VERDICT_RE = re.compile(r'StatusCode="([^"]+)"')
BEGIN_RE = re.compile(r'#beginTestCaseResult\s+(\S+)')


def parse_qpa(path):
    cases = {}
    with open(path, 'r', errors="replace") as f:
        text = f.read()
    cur = None
    for line in text.splitlines():
        m = BEGIN_RE.match(line)
        if m:
            cur = m.group(1)
            continue
        if cur and line.startswith('#endTestCaseResult'):
            cur = None
            continue
        if cur:
            vm = VERDICT_RE.search(line)
            if vm:
                cases.setdefault(cur, vm.group(1))
    return cases


def summary(stock, patched):
    keys = sorted(set(stock) | set(patched))
    unchanged = improvements = regressions = different = 0
    only_stock = only_patched = 0
    spec_change = {}
    for k in keys:
        s = stock.get(k)
        p = patched.get(k)
        if s is None:
            only_patched += 1
        elif p is None:
            only_stock += 1
        elif s == p:
            unchanged += 1
        elif s == 'Pass' and p != 'Pass':
            regressions += 1
            spec_change[k] = ('REGRESSION', s, p)
        elif s != 'Pass' and p == 'Pass':
            improvements += 1
            spec_change[k] = ('IMPROVEMENT', s, p)
        else:
            different += 1
            spec_change[k] = ('DIFFERENT', s, p)
    return (unchanged, improvements, regressions, different,
            only_stock, only_patched, spec_change)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 4
    stock = parse_qpa(sys.argv[1])
    patched = parse_qpa(sys.argv[2])

    if not stock or not patched:
        print('ERROR: one or both qpa logs are empty (check the deqp-vk run)')
        return 4

    unchanged, improvements, regressions, different, only_stock, only_patched, change = \
        summary(stock, patched)

    print('=== differential CTS result (stock vs patched) ===')
    print(f'unchanged      : {unchanged}')
    print(f'improvements   : {improvements}')
    print(f'regressions    : {regressions}')
    print(f'different      : {different}')
    print(f'only-in-stock  : {only_stock}')
    print(f'only-in-patched: {only_patched}')

    if change:
        print('\nper-case changes:')
        for k in sorted(change):
            tag, s, p = change[k]
            print(f'  [{tag:10s}] {k}  stock={s} patched={p}')

    print(f'\nCTS_DIFF regression={regressions} improvement={improvements} '
          f'different={different} unchanged={unchanged} '
          f'only_stock={only_stock} only_patched={only_patched}')

    # Machine-facing verdict for the harness.
    if regressions:
        return 1
    if different:
        return 2
    if only_stock or only_patched:
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
