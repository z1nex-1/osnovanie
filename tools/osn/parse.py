from __future__ import annotations

import csv
import io
import json
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from .store import OsnError

HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(\.0+)?\s*(;|$)|font-size\s*:\s*0|"
    r"color\s*:\s*(#fff(fff)?|white)\b.*background(-color)?\s*:\s*(#fff(fff)?|white)|"
    r"(left|top)\s*:\s*-\d{3,}px|text-indent\s*:\s*-\d{3,}",
    re.I,
)
SKIP = {"script", "style", "noscript", "template", "svg", "head"}
BLOCK = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol", "header", "footer", "blockquote"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.hidden: list[str] = []
        self.stack: list[tuple[str, bool]] = []

    def _is_hidden(self) -> bool:
        return any(flag for _, flag in self.stack)

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag in VOID:
            if tag == "br":
                self.visible.append("\n")
            alt = attr.get("alt")
            if alt and tag == "img" and HIDDEN_STYLE.search(attr.get("style") or ""):
                self.hidden.append(alt)
            return
        hide = (
            tag in SKIP
            or "hidden" in attr
            or attr.get("aria-hidden") == "true"
            or bool(HIDDEN_STYLE.search(attr.get("style") or ""))
        )
        self.stack.append((tag, hide))
        if tag in BLOCK:
            self.visible.append("\n")

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in BLOCK:
            self.visible.append("\n")

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] in {"script", "style", "noscript", "template", "svg"}:
            return
        if self._is_hidden():
            if data.strip() and not any(t in SKIP for t, _ in self.stack):
                self.hidden.append(data.strip())
            return
        self.visible.append(data)

    def handle_comment(self, data):
        if data.strip():
            self.hidden.append(data.strip())


def parse_html(raw: bytes) -> dict:
    head = raw[:2048].decode("ascii", "ignore")
    match = re.search(r'charset=["\']?([\w-]+)', head)
    encoding = match.group(1) if match else "utf-8"
    try:
        text = raw.decode(encoding, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    parser = _Html()
    parser.feed(text)
    joined = "".join(parser.visible)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in joined.splitlines()]
    body = "\n".join(line for line in lines if line)
    return {"text": body, "hidden": parser.hidden, "parser": "html"}


NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def parse_xlsx(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter(f"{NS_MAIN}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{NS_MAIN}t")))
        out = []
        for name in sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")):
            out.append(f"# лист {Path(name).stem}")
            root = ET.fromstring(archive.read(name))
            for row in root.iter(f"{NS_MAIN}row"):
                cells = []
                for cell in row.iter(f"{NS_MAIN}c"):
                    value = cell.find(f"{NS_MAIN}v")
                    raw = value.text if value is not None else ""
                    if cell.get("t") == "s" and raw:
                        raw = shared[int(raw)]
                    if cell.get("t") == "inlineStr":
                        raw = "".join(t.text or "" for t in cell.iter(f"{NS_MAIN}t"))
                    cells.append(raw or "")
                out.append("\t".join(cells))
    return {"text": "\n".join(out), "hidden": [], "parser": "xlsx"}


def parse_docx(path: Path) -> dict:
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs, hidden = [], []
    for para in root.iter(f"{word}p"):
        visible = []
        for run in para.iter(f"{word}r"):
            props = run.find(f"{word}rPr")
            text = "".join(t.text or "" for t in run.iter(f"{word}t"))
            if props is not None and props.find(f"{word}vanish") is not None:
                hidden.append(text)
            else:
                visible.append(text)
        if "".join(visible).strip():
            paragraphs.append("".join(visible))
    return {"text": "\n".join(paragraphs), "hidden": [h for h in hidden if h.strip()], "parser": "docx"}


def parse_pdf(path: Path) -> dict:
    if not shutil.which("pdftotext"):
        raise OsnError("Для PDF нужен pdftotext (пакет poppler). Установите его или добавьте текстовую версию.")
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=120)
    if done.returncode:
        raise OsnError(f"pdftotext не прочитал файл: {done.stderr.decode('utf-8', 'replace')[:200]}")
    return {"text": done.stdout.decode("utf-8", "replace"), "hidden": [], "parser": "pdftotext"}


def image_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as fh:
        head = fh.read(26)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:2] == b"\xff\xd8":
            fh.seek(2)
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    break
                if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                    fh.read(3)
                    height, width = struct.unpack(">HH", fh.read(4))
                    return width, height
                fh.seek(struct.unpack(">H", fh.read(2))[0] - 2, 1)
    raise OsnError("Поддерживаются изображения PNG и JPEG.")


def parse_image(path: Path) -> dict:
    width, height = image_size(path)
    text = ""
    parser = "image"
    if shutil.which("tesseract"):
        done = subprocess.run(["tesseract", str(path), "stdout", "-l", "rus+eng"], capture_output=True, timeout=120)
        if done.returncode == 0:
            text = done.stdout.decode("utf-8", "replace").strip()
            parser = "image+tesseract"
    return {"text": text, "hidden": [], "parser": parser, "width": width, "height": height}


def parse_table(raw: bytes, delimiter: str) -> dict:
    text = raw.decode("utf-8-sig", "replace")
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return {"text": "\n".join("\t".join(row) for row in rows), "hidden": [], "parser": "table", "rows": len(rows)}


AUDIO = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aiff"}
VIDEO = {".mp4", ".mov", ".webm", ".mkv"}


def transcribe(path: Path) -> dict:
    import os

    model = os.environ.get("WHISPER_MODEL")
    if not (shutil.which("ffmpeg") and shutil.which("whisper-cli") and model and Path(model).exists()):
        raise OsnError("Для аудио и видео нужны ffmpeg, whisper-cli и WHISPER_MODEL с путём к модели. Без них приложите готовую расшифровку как .txt.")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        step = subprocess.run(["ffmpeg", "-y", "-i", str(path), "-ar", "16000", "-ac", "1", str(wav)], capture_output=True, timeout=600)
        if step.returncode:
            raise OsnError("ffmpeg не смог извлечь звук.")
        step = subprocess.run(["whisper-cli", "-m", model, "-l", "ru", "-f", str(wav)], capture_output=True, timeout=1800)
        if step.returncode:
            raise OsnError("whisper-cli завершился с ошибкой.")
    lines = [line.strip() for line in step.stdout.decode("utf-8", "replace").splitlines() if line.strip().startswith("[")]
    return {"text": "\n".join(lines), "hidden": [], "parser": "whisper"}


def parse_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    if suffix in {".html", ".htm"}:
        return parse_html(raw)
    if suffix in {".txt", ".md", ".rst"}:
        return {"text": raw.decode("utf-8-sig", "replace"), "hidden": [], "parser": "text"}
    if suffix == ".csv":
        return parse_table(raw, ",")
    if suffix == ".tsv":
        return parse_table(raw, "\t")
    if suffix == ".json":
        return {"text": json.dumps(json.loads(raw.decode("utf-8-sig")), ensure_ascii=False, indent=1), "hidden": [], "parser": "json"}
    if suffix == ".xlsx":
        return parse_xlsx(path)
    if suffix == ".docx":
        return parse_docx(path)
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in {".png", ".jpg", ".jpeg"}:
        return parse_image(path)
    if suffix in AUDIO | VIDEO:
        return transcribe(path)
    raise OsnError(f"Формат {suffix or 'без расширения'} не поддерживается: html, txt, md, csv, tsv, json, xlsx, docx, pdf, png, jpg, аудио, видео.")
