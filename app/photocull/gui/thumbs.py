"""Small thumbnails, decoded off the UI thread, kept in a small LRU cache.

Never loads a full-size photo into the UI: JPEG draft mode decodes at a reduced
size straight from the file (plan section 10).
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

MAX_CACHED = 200


class LRU:
    """Tiny least-recently-used map (main thread only)."""

    def __init__(self, maxsize: int = MAX_CACHED):
        self.maxsize = maxsize
        self._d: OrderedDict = OrderedDict()

    def get(self, key):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def put(self, key, value) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)

    def clear(self) -> None:
        self._d.clear()


def make_thumb(path: Path | str, size: int) -> Image.Image:
    """The photo scaled to fit size x size, upright. Cheap for JPEG (draft mode)."""
    with Image.open(path) as im:
        im.draft("RGB", (size, size))
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((size, size), Image.Resampling.BILINEAR)
        return im


def file_key(path: Path | str, size: int) -> tuple:
    """Cache key that changes when the file changes (edited in place by Photoshop)."""
    try:
        st = Path(path).stat()
        return (str(path), st.st_mtime_ns, st.st_size, size)
    except OSError:
        return (str(path), 0, 0, size)


class ThumbLoader:
    """Decode thumbnails on two background threads; the caller polls the futures."""

    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="thumbs")

    def submit(self, path: Path | str, size: int) -> Future:
        return self._pool.submit(make_thumb, path, size)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
