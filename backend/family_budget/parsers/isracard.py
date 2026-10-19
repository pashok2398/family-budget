from pathlib import Path

from . import isracard_scraper, isracard_web
from .base import MappedAdapter, ParsedRow, ParserError, validate_upload


class IsracardAdapter(MappedAdapter):
    """Isracard source identity: the website XLSX export and fetcher JSON natively, other files by explicit mapping."""

    institution = "Isracard"

    def parse(self, content: bytes, extension: str, path: Path, options: dict) -> list[ParsedRow]:
        layout = options.get("layout")
        if extension == ".json":
            validate_upload(content, extension)
            rows, self.native = isracard_scraper.parse(content, options, self.card_last_four)
            self.version = isracard_scraper.LAYOUT
            return rows
        if extension == ".xlsx" and layout in {None, isracard_web.LAYOUT} and not options.get("columns"):
            validate_upload(content, extension)
            if layout == isracard_web.LAYOUT or isracard_web.is_native(isracard_web.load_rows(content)):
                rows, self.native = isracard_web.parse(content, options, self.card_last_four)
                self.version = isracard_web.LAYOUT
                return rows
        if options.get("allow_open") is not None:
            raise ParserError("allow_open applies only to the Isracard website export")
        return super().parse(content, extension, path, options)
