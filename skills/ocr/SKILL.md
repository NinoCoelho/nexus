---
name: ocr
description: Use this whenever you need to perform OCR on scanned PDFs, images, or other documents to extract text content. Prefer over manual transcription for any non-selectable-text document.
type: procedure
role: extraction
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- The user wants to extract text from a scanned PDF, image (PNG, JPEG, TIFF), or any document where text is not selectable.
- The user says "OCR this", "read this scanned document", "extract text from image", or similar.
- Prefer this over `page.extract_text()` (pdfplumber/pypdf) when the source is raster/scanned rather than vector text.

## Prerequisites

```bash
command -v tesseract >/dev/null || { echo "missing: tesseract — install with: brew install tesseract (macOS) | apt-get install tesseract-ocr (Debian/Ubuntu)"; exit 1; }
python3 -c "import pytesseract, pdf2image, PIL" 2>/dev/null || { echo "missing: pip install pytesseract pdf2image Pillow"; exit 1; }
```

For non-English OCR, additional language packs are needed:

```bash
brew install tesseract-lang             # macOS — all languages
sudo apt-get install tesseract-ocr-por  # Debian/Ubuntu — single language (Portuguese here)
```

On Windows, point `pytesseract` at the binary explicitly: `pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"`.

## Steps

### 1. OCR a single image

```python
import pytesseract
from PIL import Image

img = Image.open("document.png")
text = pytesseract.image_to_string(img)
print(text)
```

### 2. OCR a scanned PDF (page-by-page)

```python
import pytesseract
from pdf2image import convert_from_path

images = convert_from_path("scanned.pdf")
full_text = ""
for i, image in enumerate(images):
    full_text += f"--- Page {i+1} ---\n"
    full_text += pytesseract.image_to_string(image)
    full_text += "\n\n"
print(full_text)
```

### 3. OCR with language selection

```python
text = pytesseract.image_to_string(img, lang="eng+por")
```

### 4. OCR with bounding-box data (structured extraction)

```python
data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
for i, word in enumerate(data["text"]):
    if word.strip():
        print(f"Word: {word}  |  Conf: {data['conf'][i]}  |  Pos: ({data['left'][i]}, {data['top'][i]})")
```

### 5. OCR a slice of pages

```python
images = convert_from_path("scanned.pdf", first_page=1, last_page=5)
```

## Gotchas
- **Resolution matters.** `pdf2image` defaults to 200 DPI. For poor scans, bump to 300–400: `convert_from_path("file.pdf", dpi=300)`. Higher DPI = better OCR but slower and more memory.
- **Large PDFs** — `convert_from_path` loads all pages into memory at once. For 50+ pages, batch via `first_page`/`last_page`.
- **Mixed content** — if a PDF has both selectable text and scanned pages, use `pdfplumber` first to check `page.extract_text()`; fall back to OCR only on pages with no/minimal text.
- **Tables in scanned docs** — Tesseract alone handles tables poorly. For structured tables use `img2table` or `camelot`.
