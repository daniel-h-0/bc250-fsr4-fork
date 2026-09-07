# BC-250 FSR4 V3 — EXP-042B runtime golden

V3 is the new BC-250/GFX1013 FSR4 performance release.

Same-scene Cyberpunk 2077 development test, FSR 4.1.1 INT8, FG off:

- EXP-035B2: 58 FPS
- EXP-040E: 59 FPS
- EXP-042A: 61 FPS
- **EXP-042B / V3: 63 FPS**

## Highlights

- Exact runtime-tested EXP-042B compiler path.
- 64-shader audit: 13 changed, 12 lower inverse estimate, 0 new VGPR spill regressions, 0 lower-wave regressions.
- ED7: 256 -> 168 VGPR, 1314 -> 0 VGPR spills, 320512 -> 0 scratch bytes, 4 -> 6 waves/SIMD.
- Native BC-250 signed packed dot remains disabled.
- Precompiled **CachyOS/Arch + LLVM 22 x86_64** release asset.
- One-command per-user installer.
- No system Mesa replacement.
- Reproducible Docker/source build remains available for other distributions.

## One-command precompiled install

```bash
curl -fsSL https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v3/install-v3.sh | bash
```

The installer verifies the release SHA256 and dynamic dependencies before printing the Steam launch option.

The precompiled binary is the exact build used for the 63 FPS runtime result and depends on the current CachyOS/Arch LLVM 22.1 userspace ABI. Other distributions should use the source build.
