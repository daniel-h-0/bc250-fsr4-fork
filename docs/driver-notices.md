# Driver provenance and notices

This private RADV build contains Mesa 26.2.2, inherited BC250 FSR4 modifications
and the RC9 shader implementation. Full source, the original/optimized shader
pairs and evidence are in the separate complete source archive from the same
release. The archive's `v4/manifest.json` identifies the source inputs;
`build-provenance.json` identifies the build.

The original BC250 compatibility work comes from
[dmorazasanchez/bc250-fsr4](https://github.com/dmorazasanchez/bc250-fsr4), inspected
at `6173651fa3a5a557cba2c2ff802e2d6f49881bc1`. Inherited material retains its
original notices. Only new SPDX-marked tools use `LICENSE.new-code`; that is
not a license for the whole distribution.

Mesa's license summary and complete license texts are under `licenses/`.
Static libdrm 2.4.133's complete upstream archive and notices are retained there
as well. The private Debian sysroot is a build input, not an included OS.

The exact shader payload comes from the AMD FidelityFX SDK 2.3.0 and the
BC250 project's modified LLVM/DXIL sources. `notices/AMD-SDK-LICENSE.md` retains
the complete AMD notice and its DLL exception. The other files in `notices/`
retain shader/build lineage and DXC/LLVM notices. DXC, Proton, OptiScaler,
OptiPatcher and the unmodified AMD provider are not bundled in this driver
archive; the provider recipe obtains its own upstream components.

[Complete repository provenance](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/THIRD_PARTY.md)
and the release's full source archive retain the detailed input history.
