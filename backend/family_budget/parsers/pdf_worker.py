"""Separate bounded process: no OCR guessing, network, embedded script execution or attachments."""

import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    import pymupdf
    import pdfplumber

    with pymupdf.open(sys.argv[1]) as doc:
        if doc.is_encrypted or not 0 < len(doc) <= 100:
            raise ValueError("Unsupported PDF")
        if any(len(p.get_text()) < 10 for p in doc):
            raise ValueError("Scanned or unreadable page")
    tables = []
    with pdfplumber.open(sys.argv[1]) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            found = page.extract_tables()
            if not found:
                raise ValueError("Page has no reliable table")
            for table in found:
                if len(table) < 2:
                    raise ValueError("Incomplete table")
                tables.append({"page": number, "headers": table[0], "rows": table[1:]})
    encoded = json.dumps(tables, ensure_ascii=False)
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError("Too much extracted text")
    print(encoded)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(2)
