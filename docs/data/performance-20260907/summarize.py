#!/usr/bin/env python3
"""Recompute the published averages from samples.csv using Python's standard library."""
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

rows = list(csv.DictReader((Path(__file__).parent / 'samples.csv').open()))
by_run = defaultdict(list)
by_resolution = defaultdict(list)
for row in rows:
    assert float(row['PawnCount']) == 1 and float(row['IgnoredForSummary']) == 0
    by_run[row['run']].append(row)
    by_resolution[(int(row['width']), int(row['height']), row['arm'])].append(row)
assert len(by_run) == 12 and all(len(group) == 30 for group in by_run.values())
output = []
for width, height in [(1920, 1080), (2560, 1440), (3840, 2160)]:
    arms = {}
    for arm in ['v3', 'v4']:
        group = by_resolution[(width, height, arm)]
        assert len(group) == 60
        arms[arm] = {
            'fps': 1000 / mean(float(row['FrameTime']) for row in group),
            'gpu_time_ms': mean(float(row['GPUTime']) for row in group),
        }
    output.append({
        'output': [width, height],
        **arms,
        'fps_change_percent': (arms['v4']['fps'] / arms['v3']['fps'] - 1) * 100,
        'gpu_time_change_percent': (arms['v4']['gpu_time_ms'] / arms['v3']['gpu_time_ms'] - 1) * 100,
    })
print(json.dumps(output, indent=2))
