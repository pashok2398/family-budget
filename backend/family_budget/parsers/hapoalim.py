from pathlib import Path

from . import hapoalim_scraper
from .base import MappedAdapter, ParsedRow, validate_upload


class HapoalimAdapter(MappedAdapter):
    """Bank Hapoalim source identity: fetcher JSON natively, other files by explicit mapping."""

    institution = "Bank Hapoalim"

    def parse(self, content: bytes, extension: str, path: Path, options: dict) -> list[ParsedRow]:
        if extension == ".json":
            validate_upload(content, extension)
            rows, self.native = hapoalim_scraper.parse(content, options)
            self.version = hapoalim_scraper.LAYOUT
            return rows
        return super().parse(content, extension, path, options)
