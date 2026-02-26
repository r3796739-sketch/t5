"""
PDF Parser Utility for YoppyChat
Extracts text from PDF files and splits into processable chunks.
"""

import os
import logging

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> dict:
    """
    Extract text and metadata from a PDF file using PyMuPDF (fitz).

    Returns:
        dict with keys:
            pages: list of {page_num, text, word_count}
            total_pages: int
            total_words: int
            title: str (from PDF metadata, or filename)
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF processing. Install with: pip install pymupdf"
        )

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    doc = fitz.open(file_path)
    pages = []
    total_words = 0

    # Try to get document title from metadata
    meta = doc.metadata or {}
    title = meta.get("title") or os.path.splitext(os.path.basename(file_path))[0]

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        if not text:
            continue
        word_count = len(text.split())
        total_words += word_count
        pages.append({
            "page_num": page_num + 1,
            "text": text,
            "word_count": word_count,
        })

    doc.close()

    logger.info(
        f"Extracted {len(pages)} pages ({total_words} words) from PDF: {title}"
    )
    return {
        "pages": pages,
        "total_pages": len(pages),
        "total_words": total_words,
        "title": title,
    }


def chunk_pdf_pages(pages: list, chunk_size: int = 5, overlap: int = 1) -> list:
    """
    Group PDF pages into chunks suitable for embedding.

    Args:
        pages: list from extract_text_from_pdf()['pages']
        chunk_size: number of pages per chunk
        overlap: number of overlapping pages between consecutive chunks

    Returns:
        list of dicts: {chunk_index, page_range, text, word_count}
    """
    chunks = []
    step = max(1, chunk_size - overlap)

    for i in range(0, len(pages), step):
        group = pages[i : i + chunk_size]
        combined_text = "\n\n".join(p["text"] for p in group)
        start_page = group[0]["page_num"]
        end_page = group[-1]["page_num"]

        chunks.append({
            "chunk_index": len(chunks),
            "page_range": f"pp. {start_page}–{end_page}",
            "text": combined_text,
            "word_count": sum(p["word_count"] for p in group),
        })

    return chunks
