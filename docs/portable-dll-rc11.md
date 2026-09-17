# RC11: release validation

[Install across your games](optiscaler-client.md) or use the [manual guide](beginner-guide.md).
[Client validation](../integrations/optiscaler-client/README.md#validation) is separate from the DLL rendering evidence below. RC11 preserves RC10's shader
programs and improves the optional Linux cache/driver tools.

## Exact identities

| Component | Identity |
| --- | --- |
| Distribution | `4.0.0-rc11` |
| DLL provider name | `4.1.1r11` |
| DLL size | 94,840,832 bytes |
| DLL SHA256 | `8192ea97620f8e6407bff346bf905f0d555ff73d89fe14616eb1fa5e41ab3175` |
| Driver SHA256 | `5c4d74b141f946b8b11442d9886f10b62f4ecd022065d498d79ee4ac798eea4b` |

## Current validation

| Check | Result |
| --- | --- |
| Shader rebuild | All 348 sources and validated programs match RC10. |
| DLL identity | Two bytes differ: PE checksum at offset 353 and provider label at 94840547. Reversing them reconstructs RC10's exact hash. |
| Synthetic rendering | Nine 64-frame comparisons match RC10 byte for byte, covering 1080p/1440p/4K and SDR/HDR/motion/reset/resize/RCAS. |
| Watermark | SDK-rendered `4.1.1r11` on the synthetic probe. |
| Alternative driver | Exact RC10 stripped ELF. |
| Tooling | Package/export, install/update/rollback and cache-fallback checks; 267 repository tests at release. |

The [release record](data/portable-dll-rc11.json) pins the inputs and outputs.
The [watermark image](assets/rc11-watermark-reference.png) has a separate
[capture record](data/beginner-watermark-rc11.json). CI rebuilds the DLL and
verifies its byte comparison.

## Inherited evidence and limits

| Topic | Evidence |
| --- | --- |
| Cold compilation | [RC10 measurements](legacy/research/portable-dll-rc10.md). |
| DLL game routes | [Seven RC7 checks](legacy/research/portable-dll-rc7.md), with each game's actual scope. |
| Alternative driver | [RC10 Control gameplay and System Shock menu rendering](legacy/research/driver-gameplay-rc10.md). |
| Shared cache | [Control-to-System-Shock reuse](legacy/research/cache-setup-qualification.md); System Shock reached its menu. |
| Tool portability | [Installer review](legacy/research/cache-review2.md): four isolated Linux userspaces. |

The rendering evidence covers **BC250/Linux**. Native Windows, other GPUs,
frame generation and further game integrations await qualification. Dated
records identify which release and route each result applies to.

[Release notes](release-notes-rc11.md) · [Documentation](README.md)
