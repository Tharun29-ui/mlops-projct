"""Create a metadata-only inventory for local invoice datasets."""

from __future__ import annotations

import csv
import hashlib
import random
from collections import Counter
from pathlib import Path

from PIL import Image
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "ml" / "datasets" / "source-manifests" / "dataset_inventory.csv"
SPLIT_OUTPUT = ROOT / "ml" / "datasets" / "source-manifests" / "invoice_split.csv"
SPLIT_SEED = 20261014


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    sources = [
        ("invoice_pdf", ROOT / "data" / "sample_invoices", ".pdf"),
        ("purchase_invoice_image", ROOT / "data" / "sales_test" / "purchase", ".jpeg"),
    ]
    missing_sources = [str(folder.relative_to(ROOT)) for _, folder, _ in sources if not folder.is_dir()]
    if missing_sources:
        raise FileNotFoundError(
            "Invoice source folder(s) not found: " + ", ".join(missing_sources)
        )
    rows: list[dict[str, str | int]] = []
    hashes: Counter[str] = Counter()
    pdf_pages = 0
    pdf_chars = 0
    pdf_text_pages = 0

    pdf_paths = sorted(sources[0][1].glob("*.pdf"), key=lambda item: item.name.casefold())
    test_paths = set(random.Random(SPLIT_SEED).sample(pdf_paths, min(10, len(pdf_paths))))
    split_rows = [
        {
            "relative_path": path.relative_to(ROOT).as_posix(),
            "split": "test" if path in test_paths else "development",
            "seed": SPLIT_SEED,
        }
        for path in pdf_paths
    ]
    split_by_path = {row["relative_path"]: row["split"] for row in split_rows}

    for kind, folder, extension in sources:
        for path in sorted(folder.glob(f"*{extension}"), key=lambda item: item.name.casefold()):
            digest = sha256(path)
            hashes[digest] += 1
            row: dict[str, str | int] = {
                "relative_path": path.relative_to(ROOT).as_posix(),
                "kind": kind,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "pages": "",
                "text_chars": "",
                "image_width": "",
                "image_height": "",
                "split_assignment": split_by_path.get(path.relative_to(ROOT).as_posix(), "NOT_APPLICABLE"),
                "transaction_direction": "UNKNOWN",
                "review_status": "PENDING",
            }
            if extension == ".pdf":
                reader = PdfReader(str(path))
                text_by_page = [page.extract_text() or "" for page in reader.pages]
                page_chars = sum(len(text.strip()) for text in text_by_page)
                row["pages"] = len(reader.pages)
                row["text_chars"] = page_chars
                pdf_pages += len(reader.pages)
                pdf_chars += page_chars
                pdf_text_pages += sum(bool(text.strip()) for text in text_by_page)
            elif extension in {".jpg", ".jpeg", ".png"}:
                with Image.open(path) as image:
                    row["image_width"], row["image_height"] = image.size
            rows.append(row)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with SPLIT_OUTPUT.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["relative_path", "split", "seed"])
        writer.writeheader()
        writer.writerows(split_rows)

    pdf_count = sum(row["kind"] == "invoice_pdf" for row in rows)
    image_count = sum(row["kind"] == "purchase_invoice_image" for row in rows)
    duplicate_files = sum(count for count in hashes.values() if count > 1)
    print(f"PDF files: {pdf_count}; pages: {pdf_pages}; pages with extractable text: {pdf_text_pages}; extracted characters: {pdf_chars}")
    print(f"JPEG files: {image_count}; exact duplicate files by SHA-256: {duplicate_files}")
    print(f"Inventory: {OUTPUT}")
    print(f"Split: {len(pdf_paths) - len(test_paths)} development / {len(test_paths)} test; seed={SPLIT_SEED}")
    print(f"Split manifest: {SPLIT_OUTPUT}")
    print("Transaction direction remains unknown and requires review; no invoice content is printed.")


if __name__ == "__main__":
    main()
