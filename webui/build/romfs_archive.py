"""Create and verify the optional separate RomFS archive.

The archive has one top-level ``romfs/`` folder (or, for an upstream RomFS
image, a single ``romfs.img``) so that unzipping it into the iOS app's
Documents directory produces ``Documents/romfs/<marker>`` or
``Documents/romfs.img``, both of which the app accepts.  Packing
uses the upstream ``dm/ios/tools/pack_ipa.py`` (Zip64, streaming and parallel
deflate) rather than a second zip implementation.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import zipfile

DEFAULT_MARGIN = 512 * 1024 * 1024
APP_ROMFS_PREFIX = 'Payload/DmIOS.app/romfs'
APP_ROMFS_IMAGE = APP_ROMFS_PREFIX + '.img'
IMAGE_ENTRY = 'romfs.img'


def archive_path(ipa: Path) -> Path:
    """``Game.ipa`` -> ``Game.romfs.zip``."""
    ipa = Path(ipa)
    name = ipa.stem + '.romfs.zip' if ipa.suffix.lower() == '.ipa' else ipa.name + '.romfs.zip'
    return ipa.with_name(name)


def is_image(romfs) -> bool:
    """True for a RomFS image file (upstream's default), False for an extracted folder."""
    return Path(str(romfs)).is_file()


def _marker_name(marker: str) -> str:
    marker = str(marker or '').strip().replace('\\', '/')
    if not marker or marker.startswith('/') or '..' in Path(marker).parts:
        raise ValueError('invalid RomFS marker')
    return marker


def discard_archive(archive: Path):
    """Remove a stale archive and any pack_ipa partial left by a failed run."""
    archive = Path(archive)
    for path in (archive, Path(str(archive) + '.partial')):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def prepare_archive(archive: Path, romfs: Path, marker: str, margin: int = DEFAULT_MARGIN) -> int:
    """Validate the source, clear stale output and check free space; return source bytes."""
    archive, romfs = Path(archive), Path(romfs)
    name = _marker_name(marker)
    if not (romfs / name).is_file():
        raise ValueError(f'RomFS marker is missing: {romfs / name}')
    archive.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    if is_image(romfs):
        total = Path(str(romfs)).stat().st_size
    else:
        for root, _dirs, files in os.walk(romfs):
            for filename in files:
                total += (Path(root) / filename).stat().st_size
    free = shutil.disk_usage(archive.parent).free
    if free < total + margin:
        raise OSError(f'not enough free space for the RomFS archive: need {total + margin} bytes, have {free}')
    discard_archive(archive)
    return total


def pack_command(python, pack_ipa: Path, archive: Path, romfs: Path, store: str):
    """Command for the upstream packer; the mapping keeps the top-level romfs/ folder, or romfs.img for an image."""
    entry = IMAGE_ENTRY if is_image(romfs) else 'romfs'
    return [str(python), str(pack_ipa), str(archive), '--store', str(store or ''), f'{entry}={romfs}']


def verify_archive(archive: Path, marker: str):
    """Validate the top-level layout and the marker entry's CRC."""
    archive = Path(archive)
    name = _marker_name(marker)
    try:
        with zipfile.ZipFile(archive) as value:
            names = value.namelist()
            if not names:
                raise ValueError('RomFS archive is empty')
            image = IMAGE_ENTRY in names
            for entry in names:
                if image and entry == IMAGE_ENTRY:
                    continue
                if entry not in ('romfs', 'romfs/') and not entry.startswith('romfs/'):
                    raise ValueError(f'RomFS archive entry escapes romfs/: {entry}')
            if image and len(names) != 1:
                raise ValueError('RomFS image archive holds more than romfs.img')
            wanted = IMAGE_ENTRY if image else 'romfs/' + name
            if wanted not in names:
                raise ValueError(f'RomFS archive lacks {wanted}')
            with value.open(wanted) as stream:
                while stream.read(4 * 1024 * 1024):
                    pass
            return len(names)
    except zipfile.BadZipFile as error:
        raise ValueError(f'not a zip archive: {archive}') from error


def verify_no_romfs(ipa: Path):
    """Reject an IPA that still embeds a RomFS tree."""
    ipa = Path(ipa)
    try:
        with zipfile.ZipFile(ipa) as value:
            for entry in value.namelist():
                if entry in (APP_ROMFS_PREFIX, APP_ROMFS_IMAGE) or entry.startswith(APP_ROMFS_PREFIX + '/'):
                    raise ValueError(f'IPA still embeds the RomFS: {entry}')
    except zipfile.BadZipFile as error:
        raise ValueError(f'not a zip archive: {ipa}') from error
