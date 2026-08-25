# Documentation Assets

This directory contains visual material used by the technical Markdown documentation.

## Directory structure

```text
assets/
  images/
    thesis/
      raw/        Every image object extracted from all 128 PDF pages
      chapter3/   Curated Chapter 3 system figures
      chapter4/   Curated Chapter 4 figures with descriptive names
  diagrams/
    thesis/
      chapter3/   Exact Figure 3.1 deployment model
      chapter4/   Exact copies of the original diagram-type Chapter 4 figures
    english/
      chapter4/   English diagram translations
```

## Thesis figure extraction

All image objects from the complete 128-page thesis were extracted with Poppler:

```powershell
pdfimages -png thesis/thesis_final.pdf assets/images/thesis/raw/thesis-image
```

This produces 144 PNG files, numbered `thesis-image-000.png` through `thesis-image-143.png`. The count includes soft masks because they are independent image objects inside the PDF. No extracted object is translated, cropped, redrawn or otherwise modified.

The curated Chapter 3 and Chapter 4 files were selected from that extraction and renamed according to their thesis figure numbers. Soft-mask files for Figures 4.15 and 4.16 are retained because they are part of the original PDF image objects. Markdown should normally reference the main PNG, not the mask.

## Original thesis diagrams

| Diagram | Used by |
| --- | --- |
| `diagrams/thesis/chapter3/figure-3-1-system-deployment-model.png` | System overview |
| `diagrams/thesis/chapter4/figure-4-1-system-software-overview.png` | Documentation index and firmware architecture |
| `diagrams/thesis/chapter4/figure-4-8-ds-twr-sequence.png` | UWB ranging protocol |
| `diagrams/thesis/chapter4/figure-4-10-tdma-superframe.png` | UWB ranging protocol |
| `diagrams/thesis/chapter4/figure-4-14-extended-positioning-flow.png` | Embedded positioning algorithms |

Files under `diagrams/thesis` are exact, hash-verified copies of image objects extracted from the thesis. Their original language and content are intentionally preserved. English Markdown captions and surrounding explanations provide context without altering the source figures.

Files under `diagrams/english` are translated reproductions. A translated diagram must be checked against the original source geometry before it is referenced by documentation.

| English translation | Original source |
| --- | --- |
| `english/chapter4/figure-4-1-system-software-overview-en.svg` | `thesis/chapter4/figure-4-1-system-software-overview.png` |

## Source figure mapping

### Chapter 3

| File | Thesis figure | PDF page | Printed page |
| --- | --- | ---: | ---: |
| `chapter3/figure-3-1-system-deployment-model.png` | Figure 3.1 | 40 | 23 |

### Chapter 4

| File | Thesis figure | PDF page | Printed page |
| --- | --- | ---: | ---: |
| `figure-4-1-system-software-overview.png` | Figure 4.1 | 65 | 48 |
| `figure-4-2-firmware-startup-flow.png` | Figure 4.2 | 66 | 49 |
| `figure-4-3-firmware-layering.png` | Figure 4.3 | 66 | 49 |
| `figure-4-4-firmware-update-flow.png` | Figure 4.4 | 69 | 52 |
| `figure-4-5-flash-memory-map.png` | Figure 4.5 | 70 | 53 |
| `figure-4-6-dual-sector-storage.png` | Figure 4.6 | 72 | 55 |
| `figure-4-7-flash-write-and-swap-flow.png` | Figure 4.7 | 72 | 55 |
| `figure-4-8-ds-twr-sequence.png` | Figure 4.8 | 75 | 58 |
| `figure-4-9-tag-anchor-state-flow.png` | Figure 4.9 | 76 | 59 |
| `figure-4-10-tdma-superframe.png` | Figure 4.10 | 76 | 59 |
| `figure-4-11-initial-positioning-flow.png` | Figure 4.11 | 79 | 62 |
| `figure-4-12-system-mathematical-model.png` | Figure 4.12 | 80 | 63 |
| `figure-4-13-ukf-flow.png` | Figure 4.13 | 87 | 70 |
| `figure-4-14-extended-positioning-flow.png` | Figure 4.14 | 88 | 71 |
| `figure-4-15-nlos-cir-sample.png` | Figure 4.15 | 90 | 73 |
| `figure-4-16-los-cir-sample.png` | Figure 4.16 | 90 | 73 |
| `figure-4-17-ble-discovery-flow.png` | Figure 4.17 | 94 | 77 |
| `figure-4-18-ble-connection-flow.png` | Figure 4.18 | 95 | 78 |
| `figure-4-19-ble-data-exchange-flow.png` | Figure 4.19 | 95 | 78 |
| `figure-4-20-ble-disconnect-flow.png` | Figure 4.20 | 96 | 79 |
| `figure-4-21-unified-frame-format.png` | Figure 4.21 | 97 | 80 |
| `figure-4-22-ble-routing-flow.png` | Figure 4.22 | 98 | 81 |
