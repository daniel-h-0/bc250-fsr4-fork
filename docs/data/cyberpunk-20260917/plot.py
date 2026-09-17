"""Render the measured Cyberpunk matrix from outcomes.json. No hand-entered FPS.

Run: python docs/data/cyberpunk-20260917/plot.py
Outputs: PNG (2x), SVG and PDF in docs/assets; render manifest beside the dataset.
"""
from pathlib import Path
import hashlib
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from PIL import Image, ImageFilter
import numpy as np
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, Rectangle
from matplotlib.path import Path as PlotPath

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parents[1] / 'assets'
OUT.mkdir(exist_ok=True)
SOURCE = ROOT / 'outcomes.json'
data = json.loads(SOURCE.read_text())['outcomes']
lookup = {(r['resolution'], r['upscaler'], r['mode']): r for r in data}
assert len(lookup) == 18
validation = json.loads((ROOT / 'validation.json').read_text())
proof = {r['run']: r for r in validation['runs']}
for row in data:
    raw = (ROOT / row['summary_file']).read_bytes()
    summary = json.loads(raw)['Data']
    assert hashlib.sha256(raw).hexdigest() == proof[row['run']]['summary_sha256']
    assert row['average_fps'] == summary['averageFps'] == proof[row['run']]['average_fps']
    assert row['resolution'] == f"{summary['renderWidth']}x{summary['renderHeight']}"
    assert not any(summary[k] for k in ['verticalSync', 'FSR3FrameGenEnabled',
                                      'DLSSFrameGenEnabled', 'XeSSFrameGenEnabled',
                                      'DRSEnabled', 'rayTracingEnabled', 'rayTracedPathTracingEnabled'])
    assert summary['fpsClamp'] == -1
    if row['upscaler'] == 'Off':
        assert not any(summary[k] for k in ['FSR3Enabled', 'FSR2Enabled', 'FSR4Enabled',
                                          'DLSSEnabled', 'DLAAEnabled', 'XeSSEnabled'])
    else:
        assert summary['FSR3Enabled']
        assert summary['FSR3Quality'] == {'Native AA (1:1)': 1, 'Quality': 2, 'Balanced': 3, 'Performance': 4}[row['mode']]

W, H = 1720, 1120
BG, INK, MUTED, LINE = '#18232d', '#f1f4f7', '#b8c7d3', '#3d5162'
CHANGE, BORDER = '#e6b687', '#6c8395'
ROW_COLORS = ('#253541', '#1c2a35')
HEADER_BG = '#22323f'
FONTS = Path('/usr/share/fonts/liberation')
font_paths = {
    'body': FONTS / 'LiberationSans-Regular.ttf',
    'bold': FONTS / 'LiberationSans-Bold.ttf',
    'number': FONTS / 'LiberationSans-Regular.ttf',
}
assert all(p.is_file() for p in font_paths.values())
plt.rcParams.update({'svg.fonttype': 'path', 'pdf.fonttype': 42, 'svg.hashsalt': 'bc250-cp2077-matrix'})
fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)
ax = fig.add_axes([0, 0, 1, 1])
ax.set(xlim=(0, W), ylim=(H, 0))
ax.axis('off')
texts = []
displayed = []
comparisons = []
glow_texts = []

def line(x1, y1, x2, y2, color=LINE, width=1):
    ax.plot([x1, x2], [y1, y2], color=color, linewidth=width * .72, solid_capstyle='butt')

def text(x, y, value, size=24, color=INK, face='body', align='left', glow=False):
    # Coordinates and font sizes are in design pixels, independent of export DPI.
    artist = ax.text(x, y, value, fontsize=size * .72,
                    fontproperties=FontProperties(fname=font_paths[face]),
                    color=color, ha=align, va='center')
    if glow:
        glow_texts.append((x, y, value, size, face, align))
    texts.append(artist)
    return artist

def fps(cx, cy, resolution, backend, mode, size=36, color=INK, glow=False):
    record = lookup[(resolution, backend, mode)]
    label = f"{record['average_fps']:.2f}"
    text(cx, cy, label, size, color, 'bold', 'center', glow=glow)
    displayed.append({'resolution': resolution, 'upscaler': backend,
                      'mode': mode, 'label': label, 'source_fps': record['average_fps']})

def change_arrow(x1, x2, y, resolution, source_backend, source_mode, target_backend, target_mode, lower=False, background=BG):
    source = lookup[(resolution, source_backend, source_mode)]['average_fps']
    target = lookup[(resolution, target_backend, target_mode)]['average_fps']
    change = (target / source - 1) * 100
    ms_change = 1000 / target - 1000 / source
    label = f'{change:+.1f}%'.replace('-', '−')
    ms_label = f'{ms_change:+.2f} ms'.replace('-', '−')
    end_y, control_y, label_y = (119, 184, 168) if lower else (51, 92, 78)
    path = PlotPath([(x1, y + end_y), (x1 + 35, y + control_y),
                     (x2 - 35, y + control_y), (x2, y + end_y)],
                    [PlotPath.MOVETO, PlotPath.CURVE4, PlotPath.CURVE4, PlotPath.CURVE4])
    ax.add_patch(FancyArrowPatch(path=path, arrowstyle='-|>', mutation_scale=19,
                                linewidth=1.1, color=CHANGE, zorder=3))
    for offset, value, size, color in [(0, label, 27, CHANGE), (28, ms_label, 23, MUTED)]:
        annotation = text((x1 + x2) / 2, y + label_y + offset, value, size, color, align='center')
        annotation.set_bbox({'facecolor': background, 'edgecolor': 'none', 'pad': 2})
        annotation.set_zorder(4)
    if lower:
        text((x1 + x2) / 2, y + 136, 'No AA → FSR4', 18, MUTED, align='center')
    comparisons.append({'resolution': resolution, 'from': source_backend, 'from_mode': source_mode,
                        'to': target_backend, 'to_mode': target_mode,
                        'change_percent': change, 'label': label,
                        'frame_time_change_ms': ms_change, 'ms_label': ms_label,
                        'lower_arrow': lower})

text(56, 63, 'Cyberpunk 2077', 42, face='bold')
text(56, 111, 'High preset · RT off · Frame generation off · SDR', 23, MUTED)
text(W - 56, 111, 'Average FPS', 25, INK, align='right')

margin, left, right = 56, 320, W - 56
group_gap = 24
gw = (right - left - group_gap) / 2
top, split, body, row_h = 168, 232, 284, 142
bottom = body + row_h * 4 + 84
groups = [(left, '1080p', '1920x1080'),
          (left + gw + group_gap, '1440p', '2560x1440')]
cw = gw / 3
ax.add_patch(Rectangle((margin, top), left - margin, body - top,
                       facecolor=HEADER_BG, edgecolor='none', zorder=0))
text(78, (top + body) / 2, 'Mode', 23, face='bold')
for x, title, resolution in groups:
    ax.add_patch(Rectangle((x, top), gw, body - top,
                           facecolor=HEADER_BG, edgecolor='none', zorder=0))
    text(x + gw / 2, (top + split) / 2, title, 32, INK, face='bold', align='center')
    for col, label in enumerate(['No AA', 'FSR 3.0', 'FSR4.1.1-BC250-v4']):
        text(x + (col + .5) * cw, (split + body) / 2, label,
             21 if col == 2 else 25, INK,
             align='center')
    line(x, split, x + gw, split)
    # Keep column rules in the header; leave the comparison arrows unobstructed.
    for col in [1, 2]:line(x + col * cw, split, x + col * cw, body)

for x in [margin, left, left + gw, left + gw + group_gap, right]:
    line(x, top, x, bottom, BORDER, 1)
line(margin, top, right, top, BORDER, 1.3)
line(margin, body, right, body, BORDER, 1)
line(margin, bottom, right, bottom, BORDER, 1.3)

modes = [('Native', 'Native AA (1:1)'), ('Quality', 'Quality'),
         ('Balanced', 'Balanced'), ('Performance', 'Performance')]
y = body
for i, (label, mode) in enumerate(modes):
    native = mode == 'Native AA (1:1)'
    height = row_h + (84 if native else 0)
    row_bg = ROW_COLORS[i % 2]
    for start, width in [(margin, left + gw - margin), (groups[1][0], gw)]:
        ax.add_patch(Rectangle((start, y), width, height,
                               facecolor=row_bg, edgecolor='none', zorder=0))
        if i:line(start, y, start + width, y)
    text(78, y + 32, label, 27, face='bold')
    for x, _, resolution in groups:
        if native:
            fps(x + .5 * cw, y + 32, resolution, 'Off', 'Native, no AA')
            change_arrow(x + .5 * cw, x + 1.5 * cw, y, resolution,
                         'Off', 'Native, no AA', 'FSR3', mode, background=row_bg)
        else:text(x + .5 * cw, y + 32, '–', 27, MUTED, align='center')
        fps(x + 1.5 * cw, y + 32, resolution, 'FSR3', mode)
        fps(x + 2.5 * cw, y + 32, resolution, 'FSR4 RC11 INT8', mode, color=INK, glow=True)
        change_arrow(x + 1.5 * cw, x + 2.5 * cw, y, resolution,
                     'FSR3', mode, 'FSR4 RC11 INT8', mode, background=row_bg)
        if native:
            change_arrow(x + .5 * cw, x + 2.5 * cw, y, resolution,
                         'Off', 'Native, no AA', 'FSR4 RC11 INT8', mode,
                         lower=True, background=row_bg)
    y += height
assert y == bottom

text(56, 975, 'BC-250 · 40 CUs · 6 GiB VRAM + up to 6 GiB GTT · 1850 MHz GPU target', 23)
text(56, 1009, 'FSR4 RC11 INT8 via OptiScaler · Game 2.31 · GE-Proton 11-6 · RADV 26.2.2', 21, MUTED)
text(56, 1043, 'Native: 1:1 · No AA: upscaling and AA off · Built-in benchmark, one run per cell after warm-up', 21, MUTED)
text(56, 1077, 'Arrows: FPS change and added whole-frame time, left to right. Frame time = 1000 / average FPS.', 21, MUTED)

assert len(displayed) == len(data)
assert len({(r['resolution'], r['upscaler'], r['mode']) for r in displayed}) == 18
assert len(comparisons) == 12

# Render only the selected glyphs into an alpha mask, then blur it. The halo
# is a genuine soft raster layer beneath crisp text in all three exports.
glow_scale = 2
mask_fig = plt.figure(figsize=(W / 100, H / 100), dpi=100 * glow_scale)
mask_fig.patch.set_alpha(0)
mask_ax = mask_fig.add_axes([0, 0, 1, 1])
mask_ax.set(xlim=(0, W), ylim=(H, 0))
mask_ax.axis('off')
mask_ax.patch.set_alpha(0)
for x, y, value, size, face, align in glow_texts:
    mask_ax.text(x, y, value, fontsize=size * .72,
                 fontproperties=FontProperties(fname=font_paths[face]),
                 color='white', ha=align, va='center')
mask_fig.canvas.draw()
mask = Image.fromarray(np.asarray(mask_fig.canvas.buffer_rgba())[:, :, 3].copy())
plt.close(mask_fig)
near = np.asarray(mask.filter(ImageFilter.GaussianBlur(3 * glow_scale)), dtype=float)
far = np.asarray(mask.filter(ImageFilter.GaussianBlur(9 * glow_scale)), dtype=float)
halo = np.zeros((H * glow_scale, W * glow_scale, 4), dtype=np.uint8)
halo[:, :, :3] = np.round(np.array(to_rgb(CHANGE)) * 255).astype(np.uint8)
halo[:, :, 3] = np.clip(near * .20 + far * .10, 0, 255).astype(np.uint8)
ax.imshow(halo, extent=(0, W, H, 0), interpolation='bilinear', zorder=2.5, aspect='auto')

fig.canvas.draw()
renderer = fig.canvas.get_renderer()
for artist in texts:
    box = artist.get_window_extent(renderer)
    assert box.x0 >= 0 and box.y0 >= 0 and box.x1 <= W and box.y1 <= H, artist.get_text()

base = OUT / 'cyberpunk-performance-matrix'
metadata = {'Title': 'Cyberpunk 2077 — BC-250 performance matrix',
            'Description': 'Average FPS, High preset, 1080p and 1440p. Source: outcomes.json.'}
fig.savefig(base.with_suffix('.svg'), facecolor=BG, metadata=metadata)
svg = base.with_suffix('.svg')
svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
fig.savefig(base.with_suffix('.png'), dpi=200, facecolor=BG,
            metadata={'Title': metadata['Title'], 'Description': metadata['Description']})
fig.savefig(base.with_suffix('.pdf'), facecolor=BG,
            metadata={'Title': metadata['Title'], 'Subject': metadata['Description']})
(ROOT / 'render-manifest.json').write_text(json.dumps({
    'source': 'outcomes.json', 'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    'generator': 'plot.py', 'design_pixels': [W, H],
    'png_pixels': [W * 2, H * 2], 'theme': 'dark slate, alternating rows',
    'row_order': [label for label, mode in modes],
    'displayed_cells': displayed, 'comparisons': comparisons,
}, indent=2) + '\n')
plt.close(fig)
print(base.with_suffix('.png'))
