"""The photo store: files on a volume, addressed by key.

Deliberately the smallest thing that satisfies "photo key". There is no S3 or
MinIO in this stack and the spec names neither, so the choice was between
adding a container plus a client library, putting the bytes in Postgres, or
writing files to a mounted directory.

Postgres was the easy one to rule out: a few hundred kilobytes per job is
nothing to serve and a great deal to carry in every backup and every
`pg_dump`, for data no query ever looks at.

Between the other two, this is the one that adds nothing. `photo_key` is a key
into a store, the store is behind these four functions, and swapping it for S3
means rewriting this file and nothing else.

WHAT THIS IS NOT
----------------
It is not durable the way the database is. A container rebuild that loses the
volume loses the photos while the completions that reference them survive,
which is exactly the kind of half-loss that is confusing later. `read()`
returns None for a missing file rather than raising, so a photo that is gone
reads as "no photo" instead of a 500.
"""

from __future__ import annotations

import base64
import binascii
import logging
import uuid
from pathlib import Path

log = logging.getLogger("waypoint.photos")

# JPEG only. The client encodes to JPEG when it downscales, so anything else
# is a client that did not go through our code path.
EXTENSION = ".jpg"

# The first bytes of a JPEG: SOI marker, then an APPn or quantisation table.
# Checked because the alternative is trusting an unauthenticated-in-content
# blob to be what it says, and writing whatever arrives to disk under a name
# ending in .jpg.
_JPEG_MAGIC = b"\xff\xd8\xff"


def _dir(photo_dir: str) -> Path:
    path = Path(photo_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def key_for(completion_id: uuid.UUID) -> str:
    """The filename for a completion's photo.

    Derived from the completion's own id rather than generated fresh, so a
    retried upload from the offline queue overwrites the same file instead of
    leaving an orphan on the volume every time the connection drops.
    """
    return f"{completion_id}{EXTENSION}"


class BadPhoto(Exception):
    """The bytes are not a photo we will store."""


def decode(payload: str, max_bytes: int) -> bytes:
    """Base64 -> JPEG bytes, or raise.

    `validate=True` makes Python reject stray characters rather than silently
    discarding them, which is the default and is how a corrupted upload turns
    into a subtly truncated file instead of an error.
    """
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadPhoto("photo is not valid base64") from exc

    if len(raw) > max_bytes:
        raise BadPhoto(f"photo is {len(raw)} bytes, over the {max_bytes} limit")
    if not raw.startswith(_JPEG_MAGIC):
        raise BadPhoto("photo is not a JPEG")
    return raw


def write(photo_dir: str, key: str, data: bytes) -> None:
    """Store the bytes under `key`, replacing anything already there.

    Written to a temporary name and then renamed, because `rename` within a
    directory is atomic on every filesystem this runs on. A crash mid-write
    then leaves a stray temp file rather than a half-written JPEG sitting at
    the key a completion row already points at.
    """
    target = _dir(photo_dir) / key
    staging = target.with_suffix(".part")
    staging.write_bytes(data)
    staging.replace(target)


def read(photo_dir: str, key: str) -> bytes | None:
    """The stored bytes, or None if the file is not there.

    None rather than an exception: the volume can outlive or predecease the
    database, and a photo that is gone should read as "no photo" rather than
    failing the request that mentioned it.
    """
    # `key` reaches this from a URL path, so it is checked rather than trusted.
    # A key containing ".." or a separator would otherwise read any file the
    # process can see.
    if not _safe(key):
        log.warning("refused photo key %r", key)
        return None
    path = _dir(photo_dir) / key
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _safe(key: str) -> bool:
    """A key is a UUID plus .jpg, and nothing else."""
    if not key.endswith(EXTENSION):
        return False
    stem = key[: -len(EXTENSION)]
    try:
        uuid.UUID(stem)
    except ValueError:
        return False
    return True
