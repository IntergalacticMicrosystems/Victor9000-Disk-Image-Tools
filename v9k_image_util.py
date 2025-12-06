#!/usr/bin/env python3
"""
Victor 9000 Disk Image Utility

Copy files to/from and delete files on Victor 9000 disk images.
Supports FAT12 filesystem with subdirectories.
Supports both floppy and hard disk images with multiple partitions.

Usage:
    v9k_image_util.py list <image[:path]> [--json]           # List floppy
    v9k_image_util.py list <image>                            # List hard disk partitions
    v9k_image_util.py list <image:partition:path> [--json]    # List hard disk directory
    v9k_image_util.py copy <source> <dest> [--json]
    v9k_image_util.py delete <image:path> [--json]
"""

import argparse
import fnmatch
import json
import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# =============================================================================
# Constants
# =============================================================================

SECTOR_SIZE = 512
DIR_ENTRY_SIZE = 32
SECTORS_PER_CLUSTER = 4  # Victor 9000 uses 4 sectors per cluster
CLUSTER_SIZE = SECTOR_SIZE * SECTORS_PER_CLUSTER  # 2048 bytes per cluster

# FAT entry values
FAT_FREE = 0x000
FAT_BAD = 0xFF7
FAT_EOF_MIN = 0xFF8
FAT_EOF_MAX = 0xFFF

# File attributes
ATTR_READONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20

# Valid 8.3 filename characters
VALID_FILENAME_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~ ")

# Hard disk specific constants
HD_SECTORS_PER_CLUSTER = 16
HD_CLUSTER_SIZE = SECTOR_SIZE * HD_SECTORS_PER_CLUSTER  # 8192 bytes
HD_FAT_SECTORS = 8
HD_DIR_SECTORS = 20
HD_MAX_DIR_ENTRIES = 312

# Media descriptor bytes
MEDIA_FLOPPY = 0x01
MEDIA_HARDDISK = 0x02

# Hard disk label offsets
PDL_LABEL_TYPE = 0
PDL_DEVICE_ID = 2
PDL_SERIAL_NUMBER = 4
PDL_SECTOR_SIZE = 20
PDL_IPL_DISK_ADDR = 22
PDL_IPL_LOAD_ADDR = 26
PDL_IPL_LOAD_LEN = 28
PDL_IPL_CODE_ENTRY = 30
PDL_PRIMARY_BOOT_VOL = 34
PDL_CONTROLLER_PARAMS = 36

# Virtual volume label offsets
VVL_LABEL_TYPE = 0
VVL_VOLUME_NAME = 2
VVL_IPL_DISK_ADDR = 18
VVL_VOLUME_CAPACITY = 30
VVL_DATA_START = 34
VVL_HOST_BLOCK_SIZE = 38
VVL_ALLOCATION_UNIT = 40
VVL_NUM_DIR_ENTRIES = 42

# =============================================================================
# Exceptions
# =============================================================================

class V9KError(Exception):
    """Base exception for all V9K disk errors."""
    pass

class DiskError(V9KError):
    """Error reading/writing disk image."""
    pass

class DiskFullError(V9KError):
    """Not enough free space on disk."""
    pass

class DirectoryFullError(V9KError):
    """No free directory entries available."""
    pass

class InvalidFilenameError(V9KError):
    """Filename does not conform to 8.3 format."""
    pass

class FileNotFoundError(V9KError):
    """File not found in disk image."""
    pass

class CorruptedDiskError(V9KError):
    """Disk structure is corrupted."""
    pass

class PartitionError(V9KError):
    """Error related to partition operations."""
    pass

class InvalidPartitionError(PartitionError):
    """Partition index out of range or invalid."""
    pass

class HardDiskLabelError(V9KError):
    """Error parsing hard disk label structure."""
    pass

# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class DirectoryEntry:
    """Represents a 32-byte FAT directory entry."""
    name: str           # 8 chars, space-padded
    extension: str      # 3 chars, space-padded
    attributes: int     # Attribute byte
    first_cluster: int  # Starting cluster number
    file_size: int      # Size in bytes

    # Timestamps (not fully used but preserved)
    create_time: int = 0
    create_date: int = 0
    modify_time: int = 0
    modify_date: int = 0

    @classmethod
    def from_bytes(cls, data: bytes) -> 'DirectoryEntry':
        """Parse a 32-byte directory entry."""
        if len(data) != 32:
            raise DiskError(f"Invalid directory entry size: {len(data)}")

        # Decode with latin-1 to handle any byte value, then sanitize
        name = data[0:8].decode('latin-1')
        ext = data[8:11].decode('latin-1')
        attr = data[11]
        create_time = struct.unpack_from('<H', data, 14)[0]
        create_date = struct.unpack_from('<H', data, 16)[0]
        modify_time = struct.unpack_from('<H', data, 22)[0]
        modify_date = struct.unpack_from('<H', data, 24)[0]
        first_cluster = struct.unpack_from('<H', data, 26)[0]
        file_size = struct.unpack_from('<I', data, 28)[0]

        return cls(
            name=name,
            extension=ext,
            attributes=attr,
            first_cluster=first_cluster,
            file_size=file_size,
            create_time=create_time,
            create_date=create_date,
            modify_time=modify_time,
            modify_date=modify_date
        )

    def to_bytes(self) -> bytes:
        """Serialize to 32-byte directory entry."""
        data = bytearray(32)
        data[0:8] = self.name.encode('ascii')[:8].ljust(8)
        data[8:11] = self.extension.encode('ascii')[:3].ljust(3)
        data[11] = self.attributes
        struct.pack_into('<H', data, 14, self.create_time)
        struct.pack_into('<H', data, 16, self.create_date)
        struct.pack_into('<H', data, 22, self.modify_time)
        struct.pack_into('<H', data, 24, self.modify_date)
        struct.pack_into('<H', data, 26, self.first_cluster)
        struct.pack_into('<I', data, 28, self.file_size)
        return bytes(data)

    @property
    def full_name(self) -> str:
        """Return 'NAME.EXT' format."""
        name = self.name.rstrip()
        ext = self.extension.rstrip()
        if ext:
            return f"{name}.{ext}"
        return name

    @property
    def is_free(self) -> bool:
        """Check if entry is free (deleted or never used)."""
        first_byte = ord(self.name[0]) if self.name else 0
        return first_byte == 0x00 or first_byte == 0xE5

    @property
    def is_end(self) -> bool:
        """Check if this marks end of directory."""
        first_byte = ord(self.name[0]) if self.name else 0
        return first_byte == 0x00

    @property
    def is_deleted(self) -> bool:
        """Check if entry is deleted."""
        first_byte = ord(self.name[0]) if self.name else 0
        return first_byte == 0xE5

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & ATTR_DIRECTORY)

    @property
    def is_volume_label(self) -> bool:
        return bool(self.attributes & ATTR_VOLUME)

    @property
    def is_dot_entry(self) -> bool:
        """Check if this is . or .. entry."""
        return self.name.startswith('.')

    def attr_string(self) -> str:
        """Return attribute string like 'RHSDA'."""
        attrs = []
        if self.attributes & ATTR_READONLY:
            attrs.append('R')
        if self.attributes & ATTR_HIDDEN:
            attrs.append('H')
        if self.attributes & ATTR_SYSTEM:
            attrs.append('S')
        if self.attributes & ATTR_DIRECTORY:
            attrs.append('D')
        if self.attributes & ATTR_ARCHIVE:
            attrs.append('A')
        return ''.join(attrs) if attrs else '-'


@dataclass
class PhysicalDiskLabel:
    """Physical disk label at sector 0 of hard disk."""
    label_type: int
    device_id: int
    serial_number: str
    sector_size: int
    ipl_disk_address: int
    ipl_load_address: int
    ipl_load_length: int
    ipl_code_entry: int
    primary_boot_volume: int
    controller_params: bytes
    virtual_volume_addresses: list[int]

    @classmethod
    def from_bytes(cls, data: bytes) -> 'PhysicalDiskLabel':
        """Parse physical disk label from sector 0-1 data."""
        if len(data) < 512:
            raise HardDiskLabelError("Insufficient data for physical disk label")

        label_type = struct.unpack_from('<H', data, PDL_LABEL_TYPE)[0]
        device_id = struct.unpack_from('<H', data, PDL_DEVICE_ID)[0]
        serial_number = data[PDL_SERIAL_NUMBER:PDL_SERIAL_NUMBER + 16].decode('ascii', errors='replace').strip('\x00')
        sector_size = struct.unpack_from('<H', data, PDL_SECTOR_SIZE)[0]
        ipl_disk_address = struct.unpack_from('<I', data, PDL_IPL_DISK_ADDR)[0]
        ipl_load_address = struct.unpack_from('<H', data, PDL_IPL_LOAD_ADDR)[0]
        ipl_load_length = struct.unpack_from('<H', data, PDL_IPL_LOAD_LEN)[0]
        ipl_code_entry = struct.unpack_from('<I', data, PDL_IPL_CODE_ENTRY)[0]
        primary_boot_volume = struct.unpack_from('<H', data, PDL_PRIMARY_BOOT_VOL)[0]
        controller_params = data[PDL_CONTROLLER_PARAMS:PDL_CONTROLLER_PARAMS + 16]

        # Parse variable-length lists after controller params
        offset = PDL_CONTROLLER_PARAMS + 16  # 52

        # Available media list
        avail_region_count = data[offset]
        offset += 1
        # Skip available media regions (8 bytes each: 4-byte address + 4-byte size)
        offset += avail_region_count * 8

        # Working media list
        work_region_count = data[offset]
        offset += 1
        # Skip working media regions (8 bytes each)
        offset += work_region_count * 8

        # Virtual volume list
        volume_count = data[offset]
        offset += 1
        virtual_volume_addresses = []
        for _ in range(volume_count):
            addr = struct.unpack_from('<I', data, offset)[0]
            virtual_volume_addresses.append(addr)
            offset += 4

        return cls(
            label_type=label_type,
            device_id=device_id,
            serial_number=serial_number,
            sector_size=sector_size,
            ipl_disk_address=ipl_disk_address,
            ipl_load_address=ipl_load_address,
            ipl_load_length=ipl_load_length,
            ipl_code_entry=ipl_code_entry,
            primary_boot_volume=primary_boot_volume,
            controller_params=controller_params,
            virtual_volume_addresses=virtual_volume_addresses
        )


@dataclass
class VirtualVolumeLabel:
    """Virtual volume label for a partition."""
    label_type: int
    volume_name: str
    ipl_disk_address: int
    ipl_load_address: int
    ipl_load_length: int
    ipl_code_entry: int
    volume_capacity: int
    data_start: int
    host_block_size: int
    allocation_unit: int  # sectors per cluster
    num_dir_entries: int
    volume_start_sector: int  # absolute sector address of this label

    @classmethod
    def from_bytes(cls, data: bytes, volume_start_sector: int) -> 'VirtualVolumeLabel':
        """Parse virtual volume label."""
        if len(data) < 64:
            raise HardDiskLabelError("Insufficient data for virtual volume label")

        label_type = struct.unpack_from('<H', data, VVL_LABEL_TYPE)[0]
        volume_name = data[VVL_VOLUME_NAME:VVL_VOLUME_NAME + 16].decode('ascii', errors='replace').strip('\x00')
        ipl_disk_address = struct.unpack_from('<I', data, VVL_IPL_DISK_ADDR)[0]
        ipl_load_address = struct.unpack_from('<H', data, VVL_IPL_DISK_ADDR + 4)[0]
        ipl_load_length = struct.unpack_from('<H', data, VVL_IPL_DISK_ADDR + 6)[0]
        ipl_code_entry = struct.unpack_from('<I', data, VVL_IPL_DISK_ADDR + 8)[0]
        volume_capacity = struct.unpack_from('<I', data, VVL_VOLUME_CAPACITY)[0]
        data_start = struct.unpack_from('<I', data, VVL_DATA_START)[0]
        host_block_size = struct.unpack_from('<H', data, VVL_HOST_BLOCK_SIZE)[0]
        allocation_unit = struct.unpack_from('<H', data, VVL_ALLOCATION_UNIT)[0]
        num_dir_entries = struct.unpack_from('<H', data, VVL_NUM_DIR_ENTRIES)[0]

        return cls(
            label_type=label_type,
            volume_name=volume_name,
            ipl_disk_address=ipl_disk_address,
            ipl_load_address=ipl_load_address,
            ipl_load_length=ipl_load_length,
            ipl_code_entry=ipl_code_entry,
            volume_capacity=volume_capacity,
            data_start=data_start,
            host_block_size=host_block_size,
            allocation_unit=allocation_unit,
            num_dir_entries=num_dir_entries,
            volume_start_sector=volume_start_sector
        )


# =============================================================================
# Utility Functions
# =============================================================================

def validate_filename(filename: str) -> tuple[str, str]:
    """
    Validate and parse 8.3 filename.
    Returns (name, extension) both uppercase and space-padded.
    Raises InvalidFilenameError if not valid 8.3 format.
    """
    filename = filename.upper().strip()

    if not filename:
        raise InvalidFilenameError("Filename cannot be empty")

    # Split name and extension
    if '.' in filename:
        parts = filename.rsplit('.', 1)
        name = parts[0]
        ext = parts[1] if len(parts) > 1 else ''
    else:
        name = filename
        ext = ''

    # Validate lengths
    if len(name) > 8:
        raise InvalidFilenameError(f"Filename '{name}' exceeds 8 characters")
    if len(ext) > 3:
        raise InvalidFilenameError(f"Extension '{ext}' exceeds 3 characters")
    if len(name) == 0:
        raise InvalidFilenameError("Filename cannot be empty")

    # Validate characters
    for char in name:
        if char not in VALID_FILENAME_CHARS:
            raise InvalidFilenameError(f"Invalid character '{char}' in filename")
    for char in ext:
        if char not in VALID_FILENAME_CHARS:
            raise InvalidFilenameError(f"Invalid character '{char}' in extension")

    # Pad with spaces
    name = name.ljust(8)
    ext = ext.ljust(3)

    return name, ext


def parse_image_path(path_spec: str) -> tuple[str | None, int | None, str | None]:
    """
    Parse path into (image_path, partition, internal_path).

    For floppies: partition is None
    For hard disks: partition is integer 0-N

    Examples:
        'disk.img:\\FILE.COM' -> ('disk.img', None, 'FILE.COM')
        'hd.img:0:\\FILE.COM' -> ('hd.img', 0, 'FILE.COM')
        'hd.img:1:\\DIR\\F.TXT' -> ('hd.img', 1, 'DIR\\F.TXT')
        'hd.img:0:' -> ('hd.img', 0, None)
        'hd.img' -> ('hd.img', None, None)
    """
    lower = path_spec.lower()

    # Find image file extensions
    for ext in ['.img', '.ima', '.dsk']:
        idx = lower.find(ext)
        if idx != -1:
            split_pos = idx + len(ext)
            image_path = path_spec[:split_pos]
            remainder = path_spec[split_pos:]

            if not remainder:
                # Just the image path (e.g., 'disk.img')
                return (image_path, None, None)

            if remainder.startswith(':'):
                remainder = remainder[1:]  # Skip first colon

                if not remainder:
                    # Just 'disk.img:'
                    return (image_path, None, None)

                # Check if next part is a partition number
                if remainder[0].isdigit():
                    # Find where partition number ends
                    num_end = 0
                    while num_end < len(remainder) and remainder[num_end].isdigit():
                        num_end += 1
                    partition = int(remainder[:num_end])
                    after_num = remainder[num_end:]

                    if not after_num:
                        # 'hd.img:0'
                        return (image_path, partition, None)
                    elif after_num.startswith(':'):
                        # 'hd.img:0:' or 'hd.img:0:\path'
                        after_colon = after_num[1:]
                        if not after_colon:
                            return (image_path, partition, None)
                        elif after_colon.startswith('\\') or after_colon.startswith('/'):
                            return (image_path, partition, after_colon[1:] if after_colon else None)
                        else:
                            return (image_path, partition, after_colon)
                    elif after_num.startswith('\\') or after_num.startswith('/'):
                        # 'hd.img:0\path' - partition with backslash
                        return (image_path, partition, after_num[1:] if len(after_num) > 1 else None)
                    else:
                        # Invalid format
                        return (None, None, path_spec)

                # No partition number - floppy format
                if remainder.startswith('\\') or remainder.startswith('/'):
                    return (image_path, None, remainder[1:] if len(remainder) > 1 else None)
                else:
                    return (image_path, None, remainder)

            # Extension found but no colon after - just image path
            return (image_path, None, None)

    # Regular filesystem path (no recognized image extension)
    return (None, None, path_spec)


def detect_image_type(image_path: str) -> str:
    """
    Detect if image is 'floppy' or 'harddisk'.
    Uses file size and label structure heuristics.
    """
    try:
        file_size = os.path.getsize(image_path)
    except OSError:
        return 'floppy'  # Default to floppy on error

    # Size heuristic: floppies are ~600KB-1.2MB, hard disks are larger
    if file_size > 2 * 1024 * 1024:  # > 2MB likely hard disk
        return 'harddisk'

    # Read sector 0 for label detection
    try:
        with open(image_path, 'rb') as f:
            sector0 = f.read(512)

        if len(sector0) < 52:
            return 'floppy'

        # Check for hard disk label structure
        label_type = struct.unpack_from('<H', sector0, PDL_LABEL_TYPE)[0]
        device_id = struct.unpack_from('<H', sector0, PDL_DEVICE_ID)[0]

        # Hard disk label has label_type=1 and device_id=1
        if label_type == 0x0001 and device_id == 0x0001:
            return 'harddisk'

    except OSError:
        pass

    return 'floppy'


def split_internal_path(internal_path: str) -> list[str]:
    """Split internal path into components."""
    if not internal_path:
        return []
    # Remove leading backslash if present
    path = internal_path.lstrip('\\/')
    if not path:
        return []
    # Split on backslash or forward slash
    parts = []
    for part in path.replace('/', '\\').split('\\'):
        if part:
            parts.append(part.upper())
    return parts


def has_wildcards(pattern: str) -> bool:
    """Check if a string contains wildcard characters."""
    return '*' in pattern or '?' in pattern


def match_filename(pattern: str, filename: str) -> bool:
    """
    Match a DOS-style wildcard pattern against a filename.
    Supports * (any characters) and ? (single character).
    """
    pattern = pattern.upper()
    filename = filename.upper()

    # Convert DOS wildcard pattern to regex
    # * matches any characters, ? matches single character
    regex = ''
    for char in pattern:
        if char == '*':
            regex += '.*'
        elif char == '?':
            regex += '.'
        elif char in '.^$+{}[]|()\\':
            regex += '\\' + char
        else:
            regex += char

    # Anchor the pattern
    regex = '^' + regex + '$'

    return bool(re.match(regex, filename))


def match_entries(entries: list, pattern: str) -> list:
    """
    Filter directory entries by wildcard pattern.
    Returns entries whose full_name matches the pattern.
    """
    if not has_wildcards(pattern):
        # No wildcards - exact match
        pattern_upper = pattern.upper()
        return [e for e in entries if e.full_name.upper() == pattern_upper]

    return [e for e in entries if match_filename(pattern, e.full_name)]


# =============================================================================
# V9KDiskImage Class
# =============================================================================

class V9KDiskImage:
    """Main class for Victor 9000 disk image operations."""

    def __init__(self, image_path: str, readonly: bool = True):
        """Open disk image and read boot sector parameters."""
        self.image_path = image_path
        self.readonly = readonly
        self._file: BinaryIO | None = None
        self._fat_data: bytearray | None = None
        self._fat_dirty = False

        # Disk geometry (set by _read_boot_sector)
        self._sector_size = SECTOR_SIZE
        self._double_sided = False
        self._disc_type = 0
        self._data_start = 0
        self._fat_start = 1
        self._fat_sectors = 1
        self._dir_start = 0
        self._dir_sectors = 8
        self._total_clusters = 0

        # Open file
        mode = 'rb' if readonly else 'r+b'
        try:
            self._file = open(image_path, mode)
        except OSError as e:
            raise DiskError(f"Cannot open disk image: {e}")

        self._read_boot_sector()
        self._load_fat()

    def _read_boot_sector(self) -> None:
        """Parse boot sector to determine disk geometry."""
        boot = self.read_sector(0)

        # Sector size at offset 26-27
        self._sector_size = struct.unpack_from('<H', boot, 26)[0]
        if self._sector_size != 512:
            # Default to 512 if not set properly
            self._sector_size = 512

        # Flags at offset 32-33
        flags = struct.unpack_from('<H', boot, 32)[0]
        self._double_sided = bool(flags & 0x01)

        # Disc type at offset 34
        self._disc_type = boot[34]

        # Data start at offset 28-29
        self._data_start = struct.unpack_from('<H', boot, 28)[0]

        # Set geometry based on single/double sided
        if self._double_sided:
            self._fat_start = 1
            self._fat_sectors = 2
            self._dir_start = 5
            self._dir_sectors = 8
            if self._data_start == 0:
                self._data_start = 13
            # Calculate total clusters (data sectors / 1 sector per cluster)
            # Double-sided: sectors 13-2390 = 2378 data sectors
            self._total_clusters = 2378
        else:
            self._fat_start = 1
            self._fat_sectors = 1
            self._dir_start = 3
            self._dir_sectors = 8
            if self._data_start == 0:
                self._data_start = 11
            # Single-sided: sectors 11-1224 = 1214 data sectors
            self._total_clusters = 1214

    def read_sector(self, sector_num: int) -> bytes:
        """Read a single sector from the disk image."""
        if self._file is None:
            raise DiskError("Disk image not open")

        offset = sector_num * SECTOR_SIZE
        self._file.seek(offset)
        data = self._file.read(SECTOR_SIZE)

        if len(data) < SECTOR_SIZE:
            # Pad with zeros if at end of file
            data = data + bytes(SECTOR_SIZE - len(data))

        return data

    def write_sector(self, sector_num: int, data: bytes) -> None:
        """Write a single sector to the disk image."""
        if self._file is None:
            raise DiskError("Disk image not open")
        if self.readonly:
            raise DiskError("Disk image opened in read-only mode")

        if len(data) != SECTOR_SIZE:
            raise DiskError(f"Invalid sector size: {len(data)}")

        offset = sector_num * SECTOR_SIZE
        self._file.seek(offset)
        self._file.write(data)

    def _load_fat(self) -> None:
        """Load FAT into memory."""
        fat_data = bytearray()
        for i in range(self._fat_sectors):
            sector = self.read_sector(self._fat_start + i)
            fat_data.extend(sector)
        self._fat_data = fat_data
        self._fat_dirty = False

    def _write_fat(self) -> None:
        """Write FAT back to disk (both copies)."""
        if self._fat_data is None or not self._fat_dirty:
            return

        # Write FAT copy 1
        for i in range(self._fat_sectors):
            start = i * SECTOR_SIZE
            end = start + SECTOR_SIZE
            self.write_sector(self._fat_start + i, bytes(self._fat_data[start:end]))

        # Write FAT copy 2
        fat2_start = self._fat_start + self._fat_sectors
        for i in range(self._fat_sectors):
            start = i * SECTOR_SIZE
            end = start + SECTOR_SIZE
            self.write_sector(fat2_start + i, bytes(self._fat_data[start:end]))

        self._fat_dirty = False

    def get_fat_entry(self, cluster: int) -> int:
        """Read a 12-bit FAT entry."""
        if self._fat_data is None:
            raise DiskError("FAT not loaded")

        offset = cluster + (cluster // 2)  # 1.5 bytes per entry

        if offset + 1 >= len(self._fat_data):
            return FAT_FREE

        # Read 2 bytes at offset (little-endian)
        word = self._fat_data[offset] | (self._fat_data[offset + 1] << 8)

        if cluster % 2 == 0:
            # Even cluster: use lower 12 bits
            return word & 0x0FFF
        else:
            # Odd cluster: use upper 12 bits
            return word >> 4

    def set_fat_entry(self, cluster: int, value: int) -> None:
        """Write a 12-bit FAT entry."""
        if self._fat_data is None:
            raise DiskError("FAT not loaded")

        offset = cluster + (cluster // 2)

        if offset + 1 >= len(self._fat_data):
            raise DiskError(f"FAT offset out of range: {offset}")

        # Read existing 2 bytes
        word = self._fat_data[offset] | (self._fat_data[offset + 1] << 8)

        if cluster % 2 == 0:
            # Even: preserve upper 4 bits, set lower 12
            word = (word & 0xF000) | (value & 0x0FFF)
        else:
            # Odd: preserve lower 4 bits, set upper 12
            word = (word & 0x000F) | ((value & 0x0FFF) << 4)

        # Write back
        self._fat_data[offset] = word & 0xFF
        self._fat_data[offset + 1] = (word >> 8) & 0xFF

        self._fat_dirty = True

    def follow_chain(self, start_cluster: int) -> list[int]:
        """Return list of all clusters in chain starting at start_cluster."""
        if start_cluster == 0:
            return []

        chain = []
        cluster = start_cluster
        seen = set()

        while 0x002 <= cluster <= 0xFEF:
            if cluster in seen:
                raise CorruptedDiskError(f"Circular cluster chain at {cluster}")
            seen.add(cluster)
            chain.append(cluster)
            cluster = self.get_fat_entry(cluster)

        return chain

    def find_free_cluster(self) -> int | None:
        """Find a free cluster. Returns None if disk is full."""
        for cluster in range(2, self._total_clusters + 2):
            if self.get_fat_entry(cluster) == FAT_FREE:
                return cluster
        return None

    def allocate_chain(self, num_clusters: int) -> list[int]:
        """Allocate a chain of free clusters."""
        if num_clusters == 0:
            return []

        free_clusters = []
        for cluster in range(2, self._total_clusters + 2):
            if self.get_fat_entry(cluster) == FAT_FREE:
                free_clusters.append(cluster)
                if len(free_clusters) == num_clusters:
                    break

        if len(free_clusters) < num_clusters:
            raise DiskFullError(f"Need {num_clusters} clusters, only {len(free_clusters)} free")

        # Link clusters together
        for i, cluster in enumerate(free_clusters[:-1]):
            self.set_fat_entry(cluster, free_clusters[i + 1])

        # Mark last cluster as EOF
        self.set_fat_entry(free_clusters[-1], 0xFFF)

        return free_clusters

    def free_chain(self, start_cluster: int) -> None:
        """Free all clusters in a chain."""
        clusters = self.follow_chain(start_cluster)
        for cluster in clusters:
            self.set_fat_entry(cluster, FAT_FREE)

    def read_root_directory(self) -> list[DirectoryEntry]:
        """Read all entries from root directory."""
        entries = []
        for i in range(self._dir_sectors):
            sector_data = self.read_sector(self._dir_start + i)
            for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                offset = j * DIR_ENTRY_SIZE
                entry_data = sector_data[offset:offset + DIR_ENTRY_SIZE]
                entry = DirectoryEntry.from_bytes(entry_data)

                if entry.is_end:
                    return entries
                if not entry.is_free and not entry.is_volume_label:
                    entries.append(entry)

        return entries

    def read_subdirectory(self, start_cluster: int) -> list[DirectoryEntry]:
        """Read all entries from a subdirectory."""
        entries = []
        clusters = self.follow_chain(start_cluster)

        for cluster in clusters:
            # Read all sectors in this cluster
            first_sector = self._data_start + (cluster - 2) * SECTORS_PER_CLUSTER
            for sec_offset in range(SECTORS_PER_CLUSTER):
                sector_data = self.read_sector(first_sector + sec_offset)

                for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                    offset = j * DIR_ENTRY_SIZE
                    entry_data = sector_data[offset:offset + DIR_ENTRY_SIZE]
                    entry = DirectoryEntry.from_bytes(entry_data)

                    if entry.is_end:
                        return entries
                    if not entry.is_free and not entry.is_volume_label:
                        entries.append(entry)

        return entries

    def read_directory(self, cluster: int | None = None) -> list[DirectoryEntry]:
        """Read directory entries. cluster=None for root directory."""
        if cluster is None:
            return self.read_root_directory()
        return self.read_subdirectory(cluster)

    def resolve_path(self, path_components: list[str]) -> tuple[int | None, DirectoryEntry | None]:
        """
        Resolve path to directory cluster and final entry.
        Returns (directory_cluster, entry) where:
        - directory_cluster is None for root directory
        - entry is None if path refers to a directory (not a file)
        - entry is the file entry if path refers to a file
        """
        if not path_components:
            return (None, None)  # Root directory

        current_cluster: int | None = None  # Start at root

        for i, component in enumerate(path_components):
            is_last = (i == len(path_components) - 1)

            # Validate component name
            name, ext = validate_filename(component)

            # Search current directory
            entries = self.read_directory(current_cluster)
            found = None

            for entry in entries:
                if entry.name == name and entry.extension == ext:
                    found = entry
                    break

            if found is None:
                raise FileNotFoundError(f"'{component}' not found")

            if is_last:
                # This is the target
                if found.is_directory:
                    return (found.first_cluster, None)
                else:
                    return (current_cluster, found)
            else:
                # Must be a directory to continue
                if not found.is_directory:
                    raise FileNotFoundError(f"'{component}' is not a directory")
                current_cluster = found.first_cluster

        return (current_cluster, None)

    def find_entry(self, path_components: list[str]) -> DirectoryEntry:
        """Find a file or directory entry by path."""
        if not path_components:
            raise FileNotFoundError("Empty path")

        dir_cluster, entry = self.resolve_path(path_components)

        if entry is not None:
            return entry

        # Path refers to a directory - return a synthetic entry for it
        dir_entries = self.read_directory(dir_cluster)
        # Find the "." entry which refers to self
        for e in dir_entries:
            if e.name.rstrip() == '.':
                return e

        # If no "." entry, create a synthetic one
        return DirectoryEntry(
            name='        ',
            extension='   ',
            attributes=ATTR_DIRECTORY,
            first_cluster=dir_cluster or 0,
            file_size=0
        )

    def read_file(self, path_components: list[str]) -> bytes:
        """Read complete file contents."""
        _, entry = self.resolve_path(path_components)

        if entry is None:
            raise FileNotFoundError("Path refers to a directory, not a file")

        if entry.is_directory:
            raise FileNotFoundError(f"'{entry.full_name}' is a directory")

        if entry.file_size == 0:
            return b''

        clusters = self.follow_chain(entry.first_cluster)
        data = bytearray()

        for cluster in clusters:
            # Read all sectors in this cluster
            first_sector = self._data_start + (cluster - 2) * SECTORS_PER_CLUSTER
            for sec_offset in range(SECTORS_PER_CLUSTER):
                data.extend(self.read_sector(first_sector + sec_offset))

        # Truncate to actual file size
        return bytes(data[:entry.file_size])

    def _find_free_dir_slot(self, dir_cluster: int | None) -> tuple[int, int]:
        """
        Find a free directory entry slot.
        Returns (sector_num, entry_index) for root directory, or
        (cluster, entry_index) for subdirectory.
        """
        if dir_cluster is None:
            # Root directory - fixed size
            for i in range(self._dir_sectors):
                sector_data = self.read_sector(self._dir_start + i)
                for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                    offset = j * DIR_ENTRY_SIZE
                    first_byte = sector_data[offset]
                    if first_byte == 0x00 or first_byte == 0xE5:
                        return (self._dir_start + i, j)
            raise DirectoryFullError("Root directory is full")
        else:
            # Subdirectory - can grow
            clusters = self.follow_chain(dir_cluster)
            for cluster in clusters:
                first_sector = self._data_start + (cluster - 2) * SECTORS_PER_CLUSTER
                for sec_offset in range(SECTORS_PER_CLUSTER):
                    sector_data = self.read_sector(first_sector + sec_offset)
                    for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                        offset = j * DIR_ENTRY_SIZE
                        first_byte = sector_data[offset]
                        if first_byte == 0x00 or first_byte == 0xE5:
                            # Return (cluster, sector_in_cluster, entry_in_sector)
                            return (cluster, sec_offset * (SECTOR_SIZE // DIR_ENTRY_SIZE) + j)

            # Need to allocate new cluster for directory
            new_cluster = self.find_free_cluster()
            if new_cluster is None:
                raise DiskFullError("No free clusters for directory expansion")

            # Link to chain
            last_cluster = clusters[-1] if clusters else dir_cluster
            self.set_fat_entry(last_cluster, new_cluster)
            self.set_fat_entry(new_cluster, 0xFFF)

            # Initialize new directory cluster with zeros (all sectors)
            first_sector = self._data_start + (new_cluster - 2) * SECTORS_PER_CLUSTER
            for sec_offset in range(SECTORS_PER_CLUSTER):
                self.write_sector(first_sector + sec_offset, bytes(SECTOR_SIZE))

            return (new_cluster, 0)

    def _write_dir_entry(self, location: tuple[int, int], entry: DirectoryEntry, is_root: bool) -> None:
        """Write directory entry at specified location."""
        if is_root:
            sector_num, entry_idx = location
            offset = entry_idx * DIR_ENTRY_SIZE
        else:
            cluster, entry_idx = location
            # entry_idx is the index across all sectors in the cluster
            entries_per_sector = SECTOR_SIZE // DIR_ENTRY_SIZE
            sector_in_cluster = entry_idx // entries_per_sector
            entry_in_sector = entry_idx % entries_per_sector
            sector_num = self._data_start + (cluster - 2) * SECTORS_PER_CLUSTER + sector_in_cluster
            offset = entry_in_sector * DIR_ENTRY_SIZE

        sector_data = bytearray(self.read_sector(sector_num))
        sector_data[offset:offset + DIR_ENTRY_SIZE] = entry.to_bytes()
        self.write_sector(sector_num, bytes(sector_data))

    def write_file(self, path_components: list[str], data: bytes) -> None:
        """Write file to disk image."""
        if not path_components:
            raise InvalidFilenameError("Empty path")

        # Parse path - all but last component is directory path
        dir_path = path_components[:-1]
        filename = path_components[-1]

        # Validate filename
        name, ext = validate_filename(filename)

        # Find target directory
        if dir_path:
            dir_cluster, dir_entry = self.resolve_path(dir_path)
            if dir_entry is not None and not dir_entry.is_directory:
                raise FileNotFoundError(f"'{dir_path[-1]}' is not a directory")
            # If dir_entry is None, dir_cluster points to the directory
            # If dir_entry is a directory, use its first_cluster
            if dir_entry is not None:
                dir_cluster = dir_entry.first_cluster
        else:
            dir_cluster = None  # Root directory

        # Check if file already exists
        entries = self.read_directory(dir_cluster)
        for entry in entries:
            if entry.name == name and entry.extension == ext:
                if entry.is_directory:
                    raise DiskError(f"'{filename}' is a directory")
                # Delete existing file
                self.free_chain(entry.first_cluster)
                # Mark entry as deleted
                # Find and update the entry
                self._delete_entry_by_name(dir_cluster, name, ext)
                break

        # Calculate clusters needed (4 sectors per cluster = 2048 bytes per cluster)
        num_clusters = (len(data) + CLUSTER_SIZE - 1) // CLUSTER_SIZE
        if num_clusters == 0 and len(data) == 0:
            num_clusters = 0  # Empty file needs no clusters

        # Allocate clusters
        clusters = self.allocate_chain(num_clusters) if num_clusters > 0 else []

        # Write data to clusters
        data_offset = 0
        for cluster in clusters:
            first_sector = self._data_start + (cluster - 2) * SECTORS_PER_CLUSTER
            for sec_offset in range(SECTORS_PER_CLUSTER):
                chunk = data[data_offset:data_offset + SECTOR_SIZE]
                # Pad sector with zeros if needed
                if len(chunk) < SECTOR_SIZE:
                    chunk = chunk + bytes(SECTOR_SIZE - len(chunk))
                self.write_sector(first_sector + sec_offset, chunk)
                data_offset += SECTOR_SIZE

        # Create directory entry
        import time
        now = time.localtime()
        date_val = ((now.tm_year - 1980) << 9) | (now.tm_mon << 5) | now.tm_mday
        time_val = (now.tm_hour << 11) | (now.tm_min << 5) | (now.tm_sec // 2)

        entry = DirectoryEntry(
            name=name,
            extension=ext,
            attributes=ATTR_ARCHIVE,
            first_cluster=clusters[0] if clusters else 0,
            file_size=len(data),
            create_time=time_val,
            create_date=date_val,
            modify_time=time_val,
            modify_date=date_val
        )

        # Find free slot and write entry
        location = self._find_free_dir_slot(dir_cluster)
        self._write_dir_entry(location, entry, dir_cluster is None)

        # Write FAT to disk
        self._write_fat()

    def _delete_entry_by_name(self, dir_cluster: int | None, name: str, ext: str) -> None:
        """Mark directory entry as deleted."""
        if dir_cluster is None:
            # Root directory
            for i in range(self._dir_sectors):
                sector_data = bytearray(self.read_sector(self._dir_start + i))
                for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                    offset = j * DIR_ENTRY_SIZE
                    entry_name = sector_data[offset:offset + 8].decode('latin-1')
                    entry_ext = sector_data[offset + 8:offset + 11].decode('latin-1')
                    if entry_name == name and entry_ext == ext:
                        sector_data[offset] = 0xE5  # Mark as deleted
                        self.write_sector(self._dir_start + i, bytes(sector_data))
                        return
        else:
            # Subdirectory
            clusters = self.follow_chain(dir_cluster)
            for cluster in clusters:
                first_sector = self._data_start + (cluster - 2) * SECTORS_PER_CLUSTER
                for sec_offset in range(SECTORS_PER_CLUSTER):
                    sector = first_sector + sec_offset
                    sector_data = bytearray(self.read_sector(sector))
                    for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                        offset = j * DIR_ENTRY_SIZE
                        entry_name = sector_data[offset:offset + 8].decode('latin-1')
                        entry_ext = sector_data[offset + 8:offset + 11].decode('latin-1')
                        if entry_name == name and entry_ext == ext:
                            sector_data[offset] = 0xE5  # Mark as deleted
                            self.write_sector(sector, bytes(sector_data))
                            return

    def delete_file(self, path_components: list[str]) -> None:
        """Delete a file from the disk image."""
        if not path_components:
            raise InvalidFilenameError("Empty path")

        # Find the file
        dir_path = path_components[:-1]
        filename = path_components[-1]

        name, ext = validate_filename(filename)

        # Find target directory
        if dir_path:
            dir_cluster, _ = self.resolve_path(dir_path)
        else:
            dir_cluster = None

        # Find the file entry
        entries = self.read_directory(dir_cluster)
        target = None
        for entry in entries:
            if entry.name == name and entry.extension == ext:
                target = entry
                break

        if target is None:
            raise FileNotFoundError(f"File not found: {filename}")

        if target.is_directory:
            raise DiskError(f"'{filename}' is a directory, not a file")

        # Free the cluster chain
        if target.first_cluster > 0:
            self.free_chain(target.first_cluster)

        # Mark directory entry as deleted
        self._delete_entry_by_name(dir_cluster, name, ext)

        # Write FAT
        self._write_fat()

    def list_files(self, path_components: list[str] | None = None) -> list[DirectoryEntry]:
        """List files in a directory."""
        if not path_components:
            return self.read_directory(None)

        dir_cluster, entry = self.resolve_path(path_components)

        if entry is not None:
            if entry.is_directory:
                return self.read_directory(entry.first_cluster)
            else:
                # Single file
                return [entry]

        return self.read_directory(dir_cluster)

    def list_files_recursive(
        self,
        path_components: list[str] | None = None,
        pattern: str | None = None
    ) -> list[tuple[str, DirectoryEntry]]:
        """
        Recursively list files in a directory tree.
        Returns list of (path, entry) tuples where path is the relative path.
        If pattern is provided, only matching files are returned.
        """
        results = []

        def recurse(dir_cluster: int | None, current_path: str):
            entries = self.read_directory(dir_cluster)
            for entry in entries:
                if entry.is_dot_entry:
                    continue

                entry_path = current_path + '\\' + entry.full_name if current_path else entry.full_name

                if entry.is_directory:
                    # Recurse into subdirectory
                    recurse(entry.first_cluster, entry_path)
                else:
                    # Check pattern if provided
                    if pattern is None or match_filename(pattern, entry.full_name):
                        results.append((entry_path, entry))

        # Determine starting directory
        if path_components:
            # Check if last component has wildcards
            if has_wildcards(path_components[-1]):
                # Pattern in last component - list parent directory
                if len(path_components) > 1:
                    dir_cluster, _ = self.resolve_path(path_components[:-1])
                else:
                    dir_cluster = None
                file_pattern = path_components[-1]
            else:
                # No wildcards - try to resolve as directory
                try:
                    dir_cluster, entry = self.resolve_path(path_components)
                    if entry is not None and not entry.is_directory:
                        # Single file
                        return [('\\'.join(path_components), entry)]
                    file_pattern = None
                except FileNotFoundError:
                    # Might be a pattern - try parent
                    if len(path_components) > 1:
                        dir_cluster, _ = self.resolve_path(path_components[:-1])
                    else:
                        dir_cluster = None
                    file_pattern = path_components[-1]
        else:
            dir_cluster = None
            file_pattern = pattern

        # For non-recursive wildcard, just list matching files in directory
        if file_pattern and not pattern:
            entries = self.read_directory(dir_cluster)
            base_path = '\\'.join(path_components[:-1]) if path_components and len(path_components) > 1 else ''
            for entry in entries:
                if entry.is_dot_entry:
                    continue
                if match_filename(file_pattern, entry.full_name):
                    entry_path = base_path + '\\' + entry.full_name if base_path else entry.full_name
                    results.append((entry_path, entry))
            return results

        # Recursive listing
        base_path = '\\'.join(path_components) if path_components and not has_wildcards(path_components[-1]) else ''
        recurse(dir_cluster, base_path)
        return results

    def find_matching_files(
        self,
        path_components: list[str],
        recursive: bool = False
    ) -> list[tuple[str, DirectoryEntry]]:
        """
        Find files matching a path with optional wildcards.
        Returns list of (relative_path, entry) tuples.
        """
        if not path_components:
            return []

        # Check if last component has wildcards
        last_component = path_components[-1]
        has_wildcard = has_wildcards(last_component)

        if not has_wildcard and not recursive:
            # Simple case - single file
            try:
                _, entry = self.resolve_path(path_components)
                if entry and not entry.is_directory:
                    return [(entry.full_name, entry)]
                elif entry and entry.is_directory:
                    # Directory - list all files in it
                    entries = self.read_directory(entry.first_cluster)
                    return [(e.full_name, e) for e in entries if not e.is_dot_entry and not e.is_directory]
            except FileNotFoundError:
                return []
            return []

        # Determine base directory
        if len(path_components) > 1 and not has_wildcards(path_components[-2]):
            base_path = path_components[:-1]
            pattern = last_component
        elif has_wildcard:
            base_path = path_components[:-1] if len(path_components) > 1 else []
            pattern = last_component
        else:
            base_path = path_components
            pattern = '*.*'

        # Get base directory cluster
        if base_path:
            try:
                dir_cluster, entry = self.resolve_path(base_path)
                if entry is not None and entry.is_directory:
                    dir_cluster = entry.first_cluster
            except FileNotFoundError:
                return []
        else:
            dir_cluster = None

        results = []
        base_prefix = '\\'.join(base_path) if base_path else ''

        if recursive:
            # Recursive search
            def recurse(cluster: int | None, rel_path: str):
                entries = self.read_directory(cluster)
                for entry in entries:
                    if entry.is_dot_entry:
                        continue
                    entry_rel = rel_path + '\\' + entry.full_name if rel_path else entry.full_name
                    if entry.is_directory:
                        recurse(entry.first_cluster, entry_rel)
                    elif match_filename(pattern, entry.full_name):
                        results.append((entry_rel, entry))

            recurse(dir_cluster, '')
        else:
            # Non-recursive - just list matching files in directory
            entries = self.read_directory(dir_cluster)
            for entry in entries:
                if entry.is_dot_entry or entry.is_directory:
                    continue
                if match_filename(pattern, entry.full_name):
                    results.append((entry.full_name, entry))

        return results

    def flush(self) -> None:
        """Flush any pending changes to disk."""
        if self._fat_dirty:
            self._write_fat()
        if self._file:
            self._file.flush()

    def close(self) -> None:
        """Close the disk image."""
        self.flush()
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# Alias for backward compatibility
V9KFloppyImage = V9KDiskImage


# =============================================================================
# Hard Disk Partition
# =============================================================================

class V9KPartition:
    """Represents a single partition (virtual volume) on a hard disk."""

    def __init__(
        self,
        disk: 'V9KHardDiskImage',
        partition_index: int,
        volume_label: VirtualVolumeLabel
    ):
        self.disk = disk
        self.partition_index = partition_index
        self.volume_label = volume_label
        self.readonly = disk.readonly

        # Partition geometry from volume label
        self._sectors_per_cluster = volume_label.allocation_unit or HD_SECTORS_PER_CLUSTER
        self._cluster_size = SECTOR_SIZE * self._sectors_per_cluster
        self._max_dir_entries = volume_label.num_dir_entries or HD_MAX_DIR_ENTRIES

        # Calculate directory sectors from entry count
        entries_per_sector = SECTOR_SIZE // DIR_ENTRY_SIZE
        self._dir_sectors = (self._max_dir_entries + entries_per_sector - 1) // entries_per_sector

        # Calculate total clusters from volume capacity
        # Total clusters = (volume capacity - overhead) / sectors per cluster
        # We need to calculate FAT size based on cluster count (FAT12 uses 1.5 bytes/cluster)
        total_data_sectors = volume_label.volume_capacity
        estimated_clusters = total_data_sectors // self._sectors_per_cluster
        # FAT12: 1.5 bytes per entry = (clusters * 1.5) bytes = ceil((clusters * 3) / 2) bytes
        fat_bytes = (estimated_clusters * 3 + 1) // 2
        self._fat_sectors = (fat_bytes + SECTOR_SIZE - 1) // SECTOR_SIZE
        if self._fat_sectors < 1:
            self._fat_sectors = 1

        # Calculate layout relative to volume start
        self._volume_start = volume_label.volume_start_sector
        self._fat_start = self._volume_start + 1  # FAT starts after volume label
        self._dir_start = self._fat_start + (2 * self._fat_sectors)  # After both FAT copies
        self._data_start = self._dir_start + self._dir_sectors

        # Calculate total clusters
        volume_data_sectors = volume_label.volume_capacity - (1 + 2 * self._fat_sectors + self._dir_sectors)
        self._total_clusters = volume_data_sectors // self._sectors_per_cluster

        # FAT data
        self._fat_data: bytearray | None = None
        self._fat_dirty = False
        self._load_fat()

    def _load_fat(self) -> None:
        """Load FAT into memory."""
        fat_data = bytearray()
        for i in range(self._fat_sectors):
            sector = self.disk.read_sector(self._fat_start + i)
            fat_data.extend(sector)
        self._fat_data = fat_data
        self._fat_dirty = False

    def _write_fat(self) -> None:
        """Write FAT back to disk (both copies)."""
        if self._fat_data is None or not self._fat_dirty:
            return

        # Write FAT copy 1
        for i in range(self._fat_sectors):
            start = i * SECTOR_SIZE
            end = start + SECTOR_SIZE
            self.disk.write_sector(self._fat_start + i, bytes(self._fat_data[start:end]))

        # Write FAT copy 2
        fat2_start = self._fat_start + self._fat_sectors
        for i in range(self._fat_sectors):
            start = i * SECTOR_SIZE
            end = start + SECTOR_SIZE
            self.disk.write_sector(fat2_start + i, bytes(self._fat_data[start:end]))

        self._fat_dirty = False

    def get_fat_entry(self, cluster: int) -> int:
        """Read a 12-bit FAT entry."""
        if self._fat_data is None:
            raise DiskError("FAT not loaded")

        offset = cluster + (cluster // 2)

        if offset + 1 >= len(self._fat_data):
            return FAT_FREE

        word = self._fat_data[offset] | (self._fat_data[offset + 1] << 8)

        if cluster % 2 == 0:
            return word & 0x0FFF
        else:
            return word >> 4

    def set_fat_entry(self, cluster: int, value: int) -> None:
        """Write a 12-bit FAT entry."""
        if self._fat_data is None:
            raise DiskError("FAT not loaded")

        offset = cluster + (cluster // 2)

        if offset + 1 >= len(self._fat_data):
            raise DiskError(f"FAT offset out of range: {offset}")

        word = self._fat_data[offset] | (self._fat_data[offset + 1] << 8)

        if cluster % 2 == 0:
            word = (word & 0xF000) | (value & 0x0FFF)
        else:
            word = (word & 0x000F) | ((value & 0x0FFF) << 4)

        self._fat_data[offset] = word & 0xFF
        self._fat_data[offset + 1] = (word >> 8) & 0xFF
        self._fat_dirty = True

    def follow_chain(self, start_cluster: int) -> list[int]:
        """Return list of all clusters in chain."""
        if start_cluster == 0:
            return []

        chain = []
        cluster = start_cluster
        seen = set()

        while 0x002 <= cluster <= 0xFEF:
            if cluster in seen:
                raise CorruptedDiskError(f"Circular cluster chain at {cluster}")
            seen.add(cluster)
            chain.append(cluster)
            cluster = self.get_fat_entry(cluster)

        return chain

    def find_free_cluster(self) -> int | None:
        """Find a free cluster."""
        for cluster in range(2, self._total_clusters + 2):
            if self.get_fat_entry(cluster) == FAT_FREE:
                return cluster
        return None

    def allocate_chain(self, num_clusters: int) -> list[int]:
        """Allocate a chain of free clusters."""
        if num_clusters == 0:
            return []

        free_clusters = []
        for cluster in range(2, self._total_clusters + 2):
            if self.get_fat_entry(cluster) == FAT_FREE:
                free_clusters.append(cluster)
                if len(free_clusters) == num_clusters:
                    break

        if len(free_clusters) < num_clusters:
            raise DiskFullError(f"Need {num_clusters} clusters, only {len(free_clusters)} free")

        for i, cluster in enumerate(free_clusters[:-1]):
            self.set_fat_entry(cluster, free_clusters[i + 1])
        self.set_fat_entry(free_clusters[-1], 0xFFF)

        return free_clusters

    def free_chain(self, start_cluster: int) -> None:
        """Free all clusters in a chain."""
        clusters = self.follow_chain(start_cluster)
        for cluster in clusters:
            self.set_fat_entry(cluster, FAT_FREE)

    def _cluster_to_sector(self, cluster: int) -> int:
        """Convert cluster number to absolute sector number."""
        return self._data_start + (cluster - 2) * self._sectors_per_cluster

    def read_root_directory(self) -> list[DirectoryEntry]:
        """Read all entries from root directory."""
        entries = []
        for i in range(self._dir_sectors):
            sector_data = self.disk.read_sector(self._dir_start + i)
            for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                offset = j * DIR_ENTRY_SIZE
                entry_data = sector_data[offset:offset + DIR_ENTRY_SIZE]
                entry = DirectoryEntry.from_bytes(entry_data)

                if entry.is_end:
                    return entries
                if not entry.is_free and not entry.is_volume_label:
                    entries.append(entry)

        return entries

    def read_subdirectory(self, start_cluster: int) -> list[DirectoryEntry]:
        """Read all entries from a subdirectory."""
        entries = []
        clusters = self.follow_chain(start_cluster)

        for cluster in clusters:
            first_sector = self._cluster_to_sector(cluster)
            for sec_offset in range(self._sectors_per_cluster):
                sector_data = self.disk.read_sector(first_sector + sec_offset)

                for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                    offset = j * DIR_ENTRY_SIZE
                    entry_data = sector_data[offset:offset + DIR_ENTRY_SIZE]
                    entry = DirectoryEntry.from_bytes(entry_data)

                    if entry.is_end:
                        return entries
                    if not entry.is_free and not entry.is_volume_label:
                        entries.append(entry)

        return entries

    def read_directory(self, cluster: int | None = None) -> list[DirectoryEntry]:
        """Read directory entries. cluster=None for root directory."""
        if cluster is None:
            return self.read_root_directory()
        return self.read_subdirectory(cluster)

    def resolve_path(self, path_components: list[str]) -> tuple[int | None, DirectoryEntry | None]:
        """Resolve path to directory cluster and final entry."""
        if not path_components:
            return (None, None)

        current_cluster: int | None = None

        for i, component in enumerate(path_components):
            is_last = (i == len(path_components) - 1)
            name, ext = validate_filename(component)
            entries = self.read_directory(current_cluster)
            found = None

            for entry in entries:
                if entry.name == name and entry.extension == ext:
                    found = entry
                    break

            if found is None:
                raise FileNotFoundError(f"'{component}' not found")

            if is_last:
                if found.is_directory:
                    return (found.first_cluster, None)
                else:
                    return (current_cluster, found)
            else:
                if not found.is_directory:
                    raise FileNotFoundError(f"'{component}' is not a directory")
                current_cluster = found.first_cluster

        return (current_cluster, None)

    def find_entry(self, path_components: list[str]) -> DirectoryEntry:
        """Find a file or directory entry by path."""
        if not path_components:
            raise FileNotFoundError("Empty path")

        dir_cluster, entry = self.resolve_path(path_components)

        if entry is not None:
            return entry

        dir_entries = self.read_directory(dir_cluster)
        for e in dir_entries:
            if e.name.rstrip() == '.':
                return e

        return DirectoryEntry(
            name='        ',
            extension='   ',
            attributes=ATTR_DIRECTORY,
            first_cluster=dir_cluster or 0,
            file_size=0
        )

    def read_file(self, path_components: list[str]) -> bytes:
        """Read complete file contents."""
        _, entry = self.resolve_path(path_components)

        if entry is None:
            raise FileNotFoundError("Path refers to a directory, not a file")

        if entry.is_directory:
            raise FileNotFoundError(f"'{entry.full_name}' is a directory")

        if entry.file_size == 0:
            return b''

        clusters = self.follow_chain(entry.first_cluster)
        data = bytearray()

        for cluster in clusters:
            first_sector = self._cluster_to_sector(cluster)
            for sec_offset in range(self._sectors_per_cluster):
                data.extend(self.disk.read_sector(first_sector + sec_offset))

        return bytes(data[:entry.file_size])

    def _find_free_dir_slot(self, dir_cluster: int | None) -> tuple[int, int]:
        """Find a free directory entry slot."""
        if dir_cluster is None:
            for i in range(self._dir_sectors):
                sector_data = self.disk.read_sector(self._dir_start + i)
                for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                    offset = j * DIR_ENTRY_SIZE
                    first_byte = sector_data[offset]
                    if first_byte == 0x00 or first_byte == 0xE5:
                        return (self._dir_start + i, j)
            raise DirectoryFullError("Root directory is full")
        else:
            clusters = self.follow_chain(dir_cluster)
            for cluster in clusters:
                first_sector = self._cluster_to_sector(cluster)
                for sec_offset in range(self._sectors_per_cluster):
                    sector_data = self.disk.read_sector(first_sector + sec_offset)
                    for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                        offset = j * DIR_ENTRY_SIZE
                        first_byte = sector_data[offset]
                        if first_byte == 0x00 or first_byte == 0xE5:
                            return (cluster, sec_offset * (SECTOR_SIZE // DIR_ENTRY_SIZE) + j)

            new_cluster = self.find_free_cluster()
            if new_cluster is None:
                raise DiskFullError("No free clusters for directory expansion")

            last_cluster = clusters[-1] if clusters else dir_cluster
            self.set_fat_entry(last_cluster, new_cluster)
            self.set_fat_entry(new_cluster, 0xFFF)

            first_sector = self._cluster_to_sector(new_cluster)
            for sec_offset in range(self._sectors_per_cluster):
                self.disk.write_sector(first_sector + sec_offset, bytes(SECTOR_SIZE))

            return (new_cluster, 0)

    def _write_dir_entry(self, location: tuple[int, int], entry: DirectoryEntry, is_root: bool) -> None:
        """Write directory entry at specified location."""
        if is_root:
            sector_num, entry_idx = location
            offset = entry_idx * DIR_ENTRY_SIZE
        else:
            cluster, entry_idx = location
            entries_per_sector = SECTOR_SIZE // DIR_ENTRY_SIZE
            sector_in_cluster = entry_idx // entries_per_sector
            entry_in_sector = entry_idx % entries_per_sector
            sector_num = self._cluster_to_sector(cluster) + sector_in_cluster
            offset = entry_in_sector * DIR_ENTRY_SIZE

        sector_data = bytearray(self.disk.read_sector(sector_num))
        sector_data[offset:offset + DIR_ENTRY_SIZE] = entry.to_bytes()
        self.disk.write_sector(sector_num, bytes(sector_data))

    def write_file(self, path_components: list[str], data: bytes) -> None:
        """Write file to partition."""
        if not path_components:
            raise InvalidFilenameError("Empty path")

        dir_path = path_components[:-1]
        filename = path_components[-1]
        name, ext = validate_filename(filename)

        if dir_path:
            dir_cluster, dir_entry = self.resolve_path(dir_path)
            if dir_entry is not None and not dir_entry.is_directory:
                raise FileNotFoundError(f"'{dir_path[-1]}' is not a directory")
            if dir_entry is not None:
                dir_cluster = dir_entry.first_cluster
        else:
            dir_cluster = None

        entries = self.read_directory(dir_cluster)
        for entry in entries:
            if entry.name == name and entry.extension == ext:
                if entry.is_directory:
                    raise DiskError(f"'{filename}' is a directory")
                self.free_chain(entry.first_cluster)
                self._delete_entry_by_name(dir_cluster, name, ext)
                break

        num_clusters = (len(data) + self._cluster_size - 1) // self._cluster_size
        if num_clusters == 0 and len(data) == 0:
            num_clusters = 0

        clusters = self.allocate_chain(num_clusters) if num_clusters > 0 else []

        data_offset = 0
        for cluster in clusters:
            first_sector = self._cluster_to_sector(cluster)
            for sec_offset in range(self._sectors_per_cluster):
                chunk = data[data_offset:data_offset + SECTOR_SIZE]
                if len(chunk) < SECTOR_SIZE:
                    chunk = chunk + bytes(SECTOR_SIZE - len(chunk))
                self.disk.write_sector(first_sector + sec_offset, chunk)
                data_offset += SECTOR_SIZE

        import time
        now = time.localtime()
        date_val = ((now.tm_year - 1980) << 9) | (now.tm_mon << 5) | now.tm_mday
        time_val = (now.tm_hour << 11) | (now.tm_min << 5) | (now.tm_sec // 2)

        entry = DirectoryEntry(
            name=name,
            extension=ext,
            attributes=ATTR_ARCHIVE,
            first_cluster=clusters[0] if clusters else 0,
            file_size=len(data),
            create_time=time_val,
            create_date=date_val,
            modify_time=time_val,
            modify_date=date_val
        )

        location = self._find_free_dir_slot(dir_cluster)
        self._write_dir_entry(location, entry, dir_cluster is None)
        self._write_fat()

    def _delete_entry_by_name(self, dir_cluster: int | None, name: str, ext: str) -> None:
        """Mark directory entry as deleted."""
        if dir_cluster is None:
            for i in range(self._dir_sectors):
                sector_data = bytearray(self.disk.read_sector(self._dir_start + i))
                for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                    offset = j * DIR_ENTRY_SIZE
                    entry_name = sector_data[offset:offset + 8].decode('latin-1')
                    entry_ext = sector_data[offset + 8:offset + 11].decode('latin-1')
                    if entry_name == name and entry_ext == ext:
                        sector_data[offset] = 0xE5
                        self.disk.write_sector(self._dir_start + i, bytes(sector_data))
                        return
        else:
            clusters = self.follow_chain(dir_cluster)
            for cluster in clusters:
                first_sector = self._cluster_to_sector(cluster)
                for sec_offset in range(self._sectors_per_cluster):
                    sector = first_sector + sec_offset
                    sector_data = bytearray(self.disk.read_sector(sector))
                    for j in range(SECTOR_SIZE // DIR_ENTRY_SIZE):
                        offset = j * DIR_ENTRY_SIZE
                        entry_name = sector_data[offset:offset + 8].decode('latin-1')
                        entry_ext = sector_data[offset + 8:offset + 11].decode('latin-1')
                        if entry_name == name and entry_ext == ext:
                            sector_data[offset] = 0xE5
                            self.disk.write_sector(sector, bytes(sector_data))
                            return

    def delete_file(self, path_components: list[str]) -> None:
        """Delete a file from the partition."""
        if not path_components:
            raise InvalidFilenameError("Empty path")

        dir_path = path_components[:-1]
        filename = path_components[-1]
        name, ext = validate_filename(filename)

        if dir_path:
            dir_cluster, _ = self.resolve_path(dir_path)
        else:
            dir_cluster = None

        entries = self.read_directory(dir_cluster)
        target = None
        for entry in entries:
            if entry.name == name and entry.extension == ext:
                target = entry
                break

        if target is None:
            raise FileNotFoundError(f"File not found: {filename}")

        if target.is_directory:
            raise DiskError(f"'{filename}' is a directory, not a file")

        if target.first_cluster > 0:
            self.free_chain(target.first_cluster)

        self._delete_entry_by_name(dir_cluster, name, ext)
        self._write_fat()

    def list_files(self, path_components: list[str] | None = None) -> list[DirectoryEntry]:
        """List files in a directory."""
        if not path_components:
            return self.read_directory(None)

        dir_cluster, entry = self.resolve_path(path_components)

        if entry is not None:
            if entry.is_directory:
                return self.read_directory(entry.first_cluster)
            else:
                return [entry]

        return self.read_directory(dir_cluster)

    def find_matching_files(
        self,
        path_components: list[str],
        recursive: bool = False
    ) -> list[tuple[str, DirectoryEntry]]:
        """Find files matching a path with optional wildcards."""
        if not path_components:
            return []

        last_component = path_components[-1]
        has_wildcard = has_wildcards(last_component)

        if not has_wildcard and not recursive:
            try:
                _, entry = self.resolve_path(path_components)
                if entry and not entry.is_directory:
                    return [(entry.full_name, entry)]
                elif entry and entry.is_directory:
                    entries = self.read_directory(entry.first_cluster)
                    return [(e.full_name, e) for e in entries if not e.is_dot_entry and not e.is_directory]
            except FileNotFoundError:
                return []
            return []

        if len(path_components) > 1 and not has_wildcards(path_components[-2]):
            base_path = path_components[:-1]
            pattern = last_component
        elif has_wildcard:
            base_path = path_components[:-1] if len(path_components) > 1 else []
            pattern = last_component
        else:
            base_path = path_components
            pattern = '*.*'

        if base_path:
            try:
                dir_cluster, entry = self.resolve_path(base_path)
                if entry is not None and entry.is_directory:
                    dir_cluster = entry.first_cluster
            except FileNotFoundError:
                return []
        else:
            dir_cluster = None

        results = []

        if recursive:
            def recurse(cluster: int | None, rel_path: str):
                entries = self.read_directory(cluster)
                for entry in entries:
                    if entry.is_dot_entry:
                        continue
                    entry_rel = rel_path + '\\' + entry.full_name if rel_path else entry.full_name
                    if entry.is_directory:
                        recurse(entry.first_cluster, entry_rel)
                    elif match_filename(pattern, entry.full_name):
                        results.append((entry_rel, entry))

            recurse(dir_cluster, '')
        else:
            entries = self.read_directory(dir_cluster)
            for entry in entries:
                if entry.is_dot_entry or entry.is_directory:
                    continue
                if match_filename(pattern, entry.full_name):
                    results.append((entry.full_name, entry))

        return results

    def flush(self) -> None:
        """Flush any pending changes."""
        if self._fat_dirty:
            self._write_fat()


# =============================================================================
# Hard Disk Image
# =============================================================================

class V9KHardDiskImage:
    """Victor 9000 hard disk image with multiple partitions."""

    def __init__(self, image_path: str, readonly: bool = True):
        self.image_path = image_path
        self.readonly = readonly
        self._file: BinaryIO | None = None
        self._physical_label: PhysicalDiskLabel | None = None
        self._partitions: list[V9KPartition] = []

        mode = 'rb' if readonly else 'r+b'
        try:
            self._file = open(image_path, mode)
        except OSError as e:
            raise DiskError(f"Cannot open disk image: {e}")

        self._read_physical_label()
        self._load_partitions()

    def _read_physical_label(self) -> None:
        """Parse the physical disk label from sector 0."""
        data = self.read_sector(0) + self.read_sector(1)
        self._physical_label = PhysicalDiskLabel.from_bytes(data)

    def _load_partitions(self) -> None:
        """Load all virtual volumes as partitions."""
        if self._physical_label is None:
            return

        for idx, volume_addr in enumerate(self._physical_label.virtual_volume_addresses):
            volume_data = self.read_sector(volume_addr)
            volume_label = VirtualVolumeLabel.from_bytes(volume_data, volume_addr)
            partition = V9KPartition(self, idx, volume_label)
            self._partitions.append(partition)

    def read_sector(self, sector_num: int) -> bytes:
        """Read a single sector from the disk image."""
        if self._file is None:
            raise DiskError("Disk image not open")

        offset = sector_num * SECTOR_SIZE
        self._file.seek(offset)
        data = self._file.read(SECTOR_SIZE)

        if len(data) < SECTOR_SIZE:
            data = data + bytes(SECTOR_SIZE - len(data))

        return data

    def write_sector(self, sector_num: int, data: bytes) -> None:
        """Write a single sector to the disk image."""
        if self._file is None:
            raise DiskError("Disk image not open")
        if self.readonly:
            raise DiskError("Disk image opened in read-only mode")

        if len(data) != SECTOR_SIZE:
            raise DiskError(f"Invalid sector size: {len(data)}")

        offset = sector_num * SECTOR_SIZE
        self._file.seek(offset)
        self._file.write(data)

    def get_partition(self, index: int) -> V9KPartition:
        """Get partition by index."""
        if index < 0 or index >= len(self._partitions):
            raise InvalidPartitionError(
                f"Invalid partition index: {index}. "
                f"Valid range: 0-{len(self._partitions) - 1}"
            )
        return self._partitions[index]

    @property
    def partition_count(self) -> int:
        return len(self._partitions)

    def list_partitions(self) -> list[dict]:
        """Return info about all partitions."""
        return [
            {
                'index': i,
                'name': p.volume_label.volume_name.strip(),
                'capacity': p.volume_label.volume_capacity,
                'capacity_bytes': p.volume_label.volume_capacity * SECTOR_SIZE,
                'cluster_size': p._cluster_size
            }
            for i, p in enumerate(self._partitions)
        ]

    def flush(self) -> None:
        """Flush any pending changes to disk."""
        for partition in self._partitions:
            partition.flush()
        if self._file:
            self._file.flush()

    def close(self) -> None:
        """Close the disk image."""
        self.flush()
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# =============================================================================
# Output Formatting
# =============================================================================

class OutputFormatter:
    """Handle output formatting (text or JSON)."""

    def __init__(self, json_mode: bool = False):
        self.json_mode = json_mode

    def success(self, message: str, **data) -> None:
        """Output success message."""
        if self.json_mode:
            output = {"status": "success", "message": message, **data}
            print(json.dumps(output))
        else:
            print(message)

    def error(self, message: str) -> None:
        """Output error message."""
        if self.json_mode:
            output = {"status": "error", "message": message}
            print(json.dumps(output))
        else:
            print(f"Error: {message}", file=sys.stderr)

    def list_files(self, entries: list[DirectoryEntry], path: str = "") -> None:
        """Output file listing."""
        if self.json_mode:
            files = []
            for entry in entries:
                if not entry.is_dot_entry:
                    files.append({
                        "name": entry.full_name,
                        "size": entry.file_size,
                        "attr": entry.attr_string(),
                        "cluster": entry.first_cluster,
                        "is_directory": entry.is_directory
                    })
            output = {"status": "success", "path": path or "\\", "files": files}
            print(json.dumps(output))
        else:
            if path:
                print(f"Directory of {path}")
            else:
                print("Directory of \\")
            print()

            total_files = 0
            total_bytes = 0

            for entry in entries:
                if entry.is_dot_entry:
                    continue

                if entry.is_directory:
                    size_str = "<DIR>"
                else:
                    size_str = str(entry.file_size)
                    total_bytes += entry.file_size

                total_files += 1
                print(f"  {entry.full_name:<12}  {size_str:>10}  {entry.attr_string()}")

            print()
            print(f"  {total_files} file(s)  {total_bytes:,} bytes")

    def list_partitions(self, partitions: list[dict], image_path: str = "") -> None:
        """Output partition listing."""
        if self.json_mode:
            output = {
                "status": "success",
                "image": image_path,
                "partitions": partitions
            }
            print(json.dumps(output))
        else:
            print(f"Partitions in {image_path}:")
            print()
            for p in partitions:
                capacity_mb = p['capacity_bytes'] / (1024 * 1024)
                name = p['name'] if p['name'] else f"Volume {p['index']}"
                print(f"  {p['index']}: {name:<16} {capacity_mb:>8.1f} MB")
            print()
            print(f"  {len(partitions)} partition(s)")


# =============================================================================
# Command Handlers
# =============================================================================

def cmd_list(args, formatter: OutputFormatter) -> int:
    """Handle the 'list' command."""
    image_path, partition, internal_path = parse_image_path(args.path)

    if image_path is None:
        formatter.error(f"Invalid disk image path: {args.path}")
        return 1

    try:
        image_type = detect_image_type(image_path)

        if image_type == 'harddisk':
            with V9KHardDiskImage(image_path, readonly=True) as disk:
                # If no partition specified, list partitions
                if partition is None:
                    formatter.list_partitions(disk.list_partitions(), image_path)
                    return 0

                volume = disk.get_partition(partition)
                path_components = split_internal_path(internal_path) if internal_path else None
                entries = volume.list_files(path_components)

                if internal_path:
                    display_path = f"{image_path}:{partition}:\\{internal_path}"
                else:
                    display_path = f"{image_path}:{partition}:\\"
                formatter.list_files(entries, display_path)
        else:
            # Floppy disk
            with V9KDiskImage(image_path, readonly=True) as disk:
                path_components = split_internal_path(internal_path) if internal_path else None
                entries = disk.list_files(path_components)

                display_path = f"{image_path}:\\{internal_path}" if internal_path else f"{image_path}:\\"
                formatter.list_files(entries, display_path)

        return 0

    except V9KError as e:
        formatter.error(str(e))
        return 1
    except Exception as e:
        formatter.error(f"Unexpected error: {e}")
        return 1


def cmd_copy(args, formatter: OutputFormatter) -> int:
    """Handle the 'copy' command."""
    source_image, source_partition, source_internal = parse_image_path(args.source)
    dest_image, dest_partition, dest_internal = parse_image_path(args.dest)

    recursive = getattr(args, 'recursive', False)

    # Determine direction
    if source_image is not None and source_internal is not None and dest_image is None:
        # Copy from image to filesystem
        return copy_from_image(source_image, source_partition, source_internal, args.dest, formatter, recursive)

    elif source_image is None and dest_image is not None and dest_internal is not None:
        # Copy from filesystem to image
        return copy_to_image(args.source, dest_image, dest_partition, dest_internal, formatter)

    else:
        formatter.error("Invalid source/destination. One must be image:path, one must be filesystem path.")
        return 1


def copy_from_image(
    image_path: str,
    partition: int | None,
    internal_path: str,
    dest_path: str,
    formatter: OutputFormatter,
    recursive: bool = False
) -> int:
    """Copy file(s) from disk image to filesystem. Supports wildcards."""
    try:
        path_components = split_internal_path(internal_path)
        if not path_components:
            formatter.error("No file specified in image path")
            return 1

        # Check if wildcards are used
        has_wildcard = has_wildcards(internal_path)

        image_type = detect_image_type(image_path)

        if image_type == 'harddisk':
            if partition is None:
                formatter.error("Partition number required for hard disk image (e.g., image.img:0:\\FILE)")
                return 1
            disk = V9KHardDiskImage(image_path, readonly=True)
            volume = disk.get_partition(partition)
            source_display = f"{image_path}:{partition}:\\{internal_path}"
        else:
            disk = V9KDiskImage(image_path, readonly=True)
            volume = disk
            source_display = f"{image_path}:\\{internal_path}"

        try:
            if has_wildcard or recursive:
                # Multi-file copy with wildcards
                matching_files = volume.find_matching_files(path_components, recursive)

                if not matching_files:
                    formatter.error(f"No files matching '{internal_path}'")
                    return 1

                # Destination must be a directory for multi-file copy
                dest_dir = Path(dest_path)
                dest_dir.mkdir(parents=True, exist_ok=True)

                if not dest_dir.is_dir():
                    formatter.error(f"Destination must be a directory for wildcard copy: {dest_path}")
                    return 1

                total_files = 0
                total_bytes = 0
                copied_files = []

                for rel_path, entry in matching_files:
                    if entry.is_directory:
                        continue

                    # Build destination path, preserving subdirectory structure
                    if '\\' in rel_path:
                        # Has subdirectory - create it
                        rel_dir = rel_path.rsplit('\\', 1)[0]
                        file_dest_dir = dest_dir / rel_dir.replace('\\', os.sep)
                        file_dest_dir.mkdir(parents=True, exist_ok=True)
                        dest_file = file_dest_dir / entry.full_name
                    else:
                        dest_file = dest_dir / entry.full_name

                    # Read and write the file
                    # Build full path for reading
                    if '\\' in rel_path:
                        read_path = rel_path.split('\\')
                    else:
                        read_path = [rel_path]

                    data = volume.read_file(read_path)
                    dest_file.write_bytes(data)

                    total_files += 1
                    total_bytes += len(data)
                    copied_files.append({
                        "name": rel_path,
                        "size": len(data),
                        "dest": str(dest_file)
                    })

                    if not formatter.json_mode:
                        print(f"  {rel_path} -> {dest_file} ({len(data):,} bytes)")

                formatter.success(
                    f"Copied {total_files} file(s), {total_bytes:,} bytes total",
                    source=source_display,
                    dest=dest_path,
                    files=total_files,
                    bytes=total_bytes,
                    copied=copied_files
                )

            else:
                # Single file copy
                data = volume.read_file(path_components)

                # Check if dest is a directory
                dest = Path(dest_path)
                if dest.is_dir():
                    dest = dest / path_components[-1]
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)

                dest.write_bytes(data)

                formatter.success(
                    f"Copied {len(data):,} bytes",
                    source=source_display,
                    dest=str(dest),
                    bytes=len(data)
                )
        finally:
            disk.close()

        return 0

    except V9KError as e:
        formatter.error(str(e))
        return 1
    except OSError as e:
        formatter.error(f"Filesystem error: {e}")
        return 1


def copy_to_image(source_path: str, image_path: str, partition: int | None, internal_path: str, formatter: OutputFormatter) -> int:
    """Copy file from filesystem to disk image."""
    try:
        path_components = split_internal_path(internal_path)
        if not path_components:
            formatter.error("No destination file specified in image path")
            return 1

        # Read source file
        source = Path(source_path)
        if not source.exists():
            formatter.error(f"Source file not found: {source_path}")
            return 1

        data = source.read_bytes()

        image_type = detect_image_type(image_path)

        if image_type == 'harddisk':
            if partition is None:
                formatter.error("Partition number required for hard disk image (e.g., image.img:0:\\FILE)")
                return 1
            disk = V9KHardDiskImage(image_path, readonly=False)
            volume = disk.get_partition(partition)
            dest_display = f"{image_path}:{partition}:\\{internal_path}"
        else:
            disk = V9KDiskImage(image_path, readonly=False)
            volume = disk
            dest_display = f"{image_path}:\\{internal_path}"

        try:
            volume.write_file(path_components, data)

            formatter.success(
                f"Copied {len(data):,} bytes",
                source=source_path,
                dest=dest_display,
                bytes=len(data)
            )
        finally:
            disk.close()

        return 0

    except V9KError as e:
        formatter.error(str(e))
        return 1
    except OSError as e:
        formatter.error(f"Filesystem error: {e}")
        return 1


def cmd_delete(args, formatter: OutputFormatter) -> int:
    """Handle the 'delete' command."""
    image_path, partition, internal_path = parse_image_path(args.path)

    if image_path is None or internal_path is None:
        formatter.error(f"Invalid disk image path: {args.path}")
        return 1

    try:
        path_components = split_internal_path(internal_path)
        if not path_components:
            formatter.error("No file specified to delete")
            return 1

        image_type = detect_image_type(image_path)

        if image_type == 'harddisk':
            if partition is None:
                formatter.error("Partition number required for hard disk image (e.g., image.img:0:\\FILE)")
                return 1
            disk = V9KHardDiskImage(image_path, readonly=False)
            volume = disk.get_partition(partition)
            delete_display = f"{image_path}:{partition}:\\{internal_path}"
        else:
            disk = V9KDiskImage(image_path, readonly=False)
            volume = disk
            delete_display = f"{image_path}:\\{internal_path}"

        try:
            volume.delete_file(path_components)

            formatter.success(
                f"Deleted {internal_path}",
                deleted=delete_display
            )
        finally:
            disk.close()

        return 0

    except V9KError as e:
        formatter.error(str(e))
        return 1
    except OSError as e:
        formatter.error(f"Filesystem error: {e}")
        return 1

# =============================================================================
# Extended Help
# =============================================================================

EXTENDED_HELP = """
Victor 9000 Disk Image Utility - Detailed Help
===============================================

OVERVIEW
--------
This utility manages Victor 9000 floppy and hard disk images. It supports
reading, writing, and deleting files using the FAT12 filesystem.

PATH SYNTAX
-----------
Floppy disk images:
    image.img                      Image file only (list root directory)
    image.img:\\                    Root directory
    image.img:\\FILE.COM            File in root directory
    image.img:\\SUBDIR              Subdirectory
    image.img:\\SUBDIR\\FILE.COM    File in subdirectory

Hard disk images (with partitions):
    image.img                      Image file only (list partitions)
    image.img:0:\\                  Root of partition 0
    image.img:0:\\FILE.COM          File in partition 0 root
    image.img:1:\\SUBDIR            Subdirectory in partition 1
    image.img:2:\\SUBDIR\\FILE.COM  File in subdirectory on partition 2

The image type (floppy vs hard disk) is auto-detected based on file size
and disk label structure.

COMMANDS
--------
list <path>
    List directory contents or partitions.

    For floppy images:
        v9k_image_util.py list disk.img              # List root directory
        v9k_image_util.py list disk.img:\\SUBDIR     # List subdirectory

    For hard disk images:
        v9k_image_util.py list hd.img                # List partitions
        v9k_image_util.py list hd.img:0:\\           # List partition 0 root
        v9k_image_util.py list hd.img:1:\\DOS        # List DOS dir on partition 1

copy <source> <dest>
    Copy files between disk image and local filesystem.

    Copy FROM image (floppy):
        v9k_image_util.py copy disk.img:\\FILE.COM .           # Single file
        v9k_image_util.py copy disk.img:\\*.COM c:\\temp\\     # Wildcard
        v9k_image_util.py copy disk.img:\\* c:\\temp\\ -r      # Recursive

    Copy FROM image (hard disk):
        v9k_image_util.py copy hd.img:0:\\FILE.COM .           # From partition 0
        v9k_image_util.py copy hd.img:1:\\*.* c:\\temp\\       # Wildcard

    Copy TO image:
        v9k_image_util.py copy file.txt disk.img:\\FILE.TXT   # To floppy
        v9k_image_util.py copy file.txt hd.img:0:\\FILE.TXT   # To partition 0

delete <path>
    Delete a file from the disk image.

        v9k_image_util.py delete disk.img:\\FILE.COM          # From floppy
        v9k_image_util.py delete hd.img:0:\\FILE.COM          # From partition 0

OPTIONS
-------
--json          Output in JSON format for programmatic use
-r, --recursive Copy subdirectories recursively (copy command only)
--help-syntax   Show this detailed help page

WILDCARDS
---------
The copy command supports DOS-style wildcards:
    *       Matches any characters (including none)
    ?       Matches exactly one character

Examples:
    *.COM       All .COM files
    *.          All files without extension
    *.*         All files with extensions
    *           All files (with or without extension)
    FILE?.TXT   FILE1.TXT, FILE2.TXT, etc.

FILENAME FORMAT
---------------
Victor 9000 uses standard 8.3 DOS filenames:
    - Filename: 1-8 characters
    - Extension: 0-3 characters (optional)
    - Valid characters: A-Z, 0-9, ! # $ % & ' ( ) - @ ^ _ ` { } ~
    - Filenames are case-insensitive (stored uppercase)

TECHNICAL NOTES
---------------
Floppy Disks:
    - 4 sectors per cluster (2048 bytes)
    - Single-sided: ~600KB, Double-sided: ~1.2MB
    - FAT at sectors 1-2 (SS) or 1-4 (DS)
    - Directory at sectors 3-10 (SS) or 5-12 (DS)

Hard Disks:
    - Physical disk label at sector 0
    - Multiple partitions (virtual volumes)
    - Variable cluster size (typically 64 sectors = 32KB)
    - Each partition has its own FAT and directory

EXIT CODES
----------
    0   Success
    1   Error (message printed to stderr or JSON output)
"""


def print_extended_help() -> None:
    """Print extended help documentation."""
    print(EXTENDED_HELP)


# =============================================================================
# Main Entry Point
# =============================================================================

def main() -> int:
    """Main entry point."""
    # Check for extended help before argparse
    if '--help-syntax' in sys.argv or '-H' in sys.argv:
        print_extended_help()
        return 0

    parser = argparse.ArgumentParser(
        prog='v9k_image_util',
        description='Victor 9000 disk image utility (floppy and hard disk)',
        epilog='Use --help-syntax for detailed syntax and examples.'
    )
    parser.add_argument('--json', action='store_true', help='Output in JSON format')
    parser.add_argument('--help-syntax', '-H', action='store_true',
                        help='Show detailed help with syntax and examples')

    subparsers = parser.add_subparsers(dest='command', required=True)

    # List command
    list_parser = subparsers.add_parser('list', help='List files or partitions',
                                        epilog='Use --help-syntax for path syntax.')
    list_parser.add_argument('path', help='Disk image path (image.img or image.img:N:\\path)')
    list_parser.add_argument('--json', action='store_true', help='Output in JSON format')

    # Copy command
    copy_parser = subparsers.add_parser('copy', help='Copy files to/from disk image',
                                        epilog='Use --help-syntax for path syntax.')
    copy_parser.add_argument('source', help='Source path (supports wildcards: *.COM, *.*)')
    copy_parser.add_argument('dest', help='Destination path (use directory for wildcards)')
    copy_parser.add_argument('-r', '--recursive', action='store_true',
                             help='Copy subdirectories recursively')
    copy_parser.add_argument('--json', action='store_true', help='Output in JSON format')

    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Delete file from disk image',
                                          epilog='Use --help-syntax for path syntax.')
    delete_parser.add_argument('path', help='File to delete (image.img:\\FILE or image.img:N:\\FILE)')
    delete_parser.add_argument('--json', action='store_true', help='Output in JSON format')

    args = parser.parse_args()
    formatter = OutputFormatter(json_mode=args.json)

    match args.command:
        case 'list':
            return cmd_list(args, formatter)
        case 'copy':
            return cmd_copy(args, formatter)
        case 'delete':
            return cmd_delete(args, formatter)
        case _:
            formatter.error(f"Unknown command: {args.command}")
            return 1


if __name__ == '__main__':
    sys.exit(main())
