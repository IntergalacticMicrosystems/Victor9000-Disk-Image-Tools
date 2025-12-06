#!/usr/bin/env python3
"""
Comprehensive test suite for Victor 9000 Disk Image Utility.

Run with: pytest test_v9k_image_util.py -v
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Import the module under test
from v9k_image_util import (
    # Constants
    SECTOR_SIZE, DIR_ENTRY_SIZE, SECTORS_PER_CLUSTER, CLUSTER_SIZE,
    FAT_FREE, FAT_EOF_MIN, FAT_EOF_MAX,
    ATTR_READONLY, ATTR_HIDDEN, ATTR_SYSTEM, ATTR_VOLUME, ATTR_DIRECTORY, ATTR_ARCHIVE,
    # Exceptions
    V9KError, DiskError, DiskFullError, DirectoryFullError,
    InvalidFilenameError, FileNotFoundError as V9KFileNotFoundError,
    CorruptedDiskError, InvalidPartitionError, HardDiskLabelError,
    # Data classes
    DirectoryEntry, PhysicalDiskLabel, VirtualVolumeLabel,
    # Utility functions
    validate_filename, parse_image_path, detect_image_type,
    split_internal_path, has_wildcards, match_filename, match_entries,
    # Classes
    V9KDiskImage, V9KHardDiskImage, V9KPartition, OutputFormatter,
    # Command handlers
    cmd_list, cmd_copy, cmd_delete,
)


# =============================================================================
# Test Configuration and Paths
# =============================================================================

TEST_DIR = Path(__file__).parent
EXAMPLE_DISKS_DIR = TEST_DIR / "example_disks"
REFERENCE_FILES_DIR = EXAMPLE_DISKS_DIR / "files"

# Test disk images
FLOPPY_DISK_IMG = EXAMPLE_DISKS_DIR / "disk.img"
BLANK_DS_IMG = EXAMPLE_DISKS_DIR / "v9k-blank-ds.img"
BLANK_SS_IMG = EXAMPLE_DISKS_DIR / "v9k-blank-ss.img"
HARD_DISK_IMG = EXAMPLE_DISKS_DIR / "vichd.img"


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def floppy_image_readonly():
    """Open the test floppy image in readonly mode."""
    with V9KDiskImage(str(FLOPPY_DISK_IMG), readonly=True) as disk:
        yield disk


@pytest.fixture
def floppy_image_copy(temp_dir):
    """Create a writable copy of the floppy disk image."""
    copy_path = temp_dir / "disk_copy.img"
    shutil.copy(FLOPPY_DISK_IMG, copy_path)
    return copy_path


@pytest.fixture
def blank_ds_copy(temp_dir):
    """Create a writable copy of the blank double-sided floppy."""
    copy_path = temp_dir / "blank_ds_copy.img"
    shutil.copy(BLANK_DS_IMG, copy_path)
    return copy_path


@pytest.fixture
def blank_ss_copy(temp_dir):
    """Create a writable copy of the blank single-sided floppy."""
    copy_path = temp_dir / "blank_ss_copy.img"
    shutil.copy(BLANK_SS_IMG, copy_path)
    return copy_path


@pytest.fixture
def hard_disk_readonly():
    """Open the test hard disk image in readonly mode."""
    with V9KHardDiskImage(str(HARD_DISK_IMG), readonly=True) as disk:
        yield disk


@pytest.fixture
def hard_disk_copy(temp_dir):
    """Create a writable copy of the hard disk image."""
    copy_path = temp_dir / "vichd_copy.img"
    shutil.copy(HARD_DISK_IMG, copy_path)
    return copy_path


# =============================================================================
# Helper Functions
# =============================================================================

def create_test_data(size: int) -> bytes:
    """Generate test data of a specific size."""
    pattern = b"TEST_DATA_PATTERN_"
    repetitions = (size // len(pattern)) + 1
    return (pattern * repetitions)[:size]


def compare_files(file1: Path, file2: Path) -> bool:
    """Compare two files byte-by-byte."""
    return file1.read_bytes() == file2.read_bytes()


# =============================================================================
# Unit Tests: DirectoryEntry
# =============================================================================

class TestDirectoryEntry:
    """Test DirectoryEntry data class."""

    def test_from_bytes_file(self):
        """Parse a file directory entry."""
        # Create a 32-byte directory entry for a file
        data = bytearray(32)
        data[0:8] = b"TESTFILE"  # Name
        data[8:11] = b"COM"  # Extension
        data[11] = ATTR_ARCHIVE  # Attributes
        struct.pack_into('<H', data, 26, 5)  # First cluster = 5
        struct.pack_into('<I', data, 28, 12345)  # File size

        entry = DirectoryEntry.from_bytes(bytes(data))

        assert entry.name == "TESTFILE"
        assert entry.extension == "COM"
        assert entry.attributes == ATTR_ARCHIVE
        assert entry.first_cluster == 5
        assert entry.file_size == 12345
        assert entry.full_name == "TESTFILE.COM"
        assert not entry.is_directory
        assert not entry.is_volume_label
        assert not entry.is_free
        assert not entry.is_deleted

    def test_from_bytes_directory(self):
        """Parse a directory entry."""
        data = bytearray(32)
        data[0:8] = b"SUBDIR  "
        data[8:11] = b"   "
        data[11] = ATTR_DIRECTORY
        struct.pack_into('<H', data, 26, 10)

        entry = DirectoryEntry.from_bytes(bytes(data))

        assert entry.name == "SUBDIR  "
        assert entry.is_directory
        assert entry.full_name == "SUBDIR"

    def test_from_bytes_volume_label(self):
        """Parse a volume label entry."""
        data = bytearray(32)
        data[0:8] = b"VOLUME  "
        data[8:11] = b"   "
        data[11] = ATTR_VOLUME

        entry = DirectoryEntry.from_bytes(bytes(data))

        assert entry.is_volume_label
        assert not entry.is_directory

    def test_to_bytes_roundtrip(self):
        """Test serialization and deserialization roundtrip."""
        original = DirectoryEntry(
            name="TEST    ",
            extension="TXT",
            attributes=ATTR_ARCHIVE,
            first_cluster=100,
            file_size=5000,
            create_time=0x5000,
            create_date=0x4821,
            modify_time=0x5100,
            modify_date=0x4822
        )

        serialized = original.to_bytes()
        restored = DirectoryEntry.from_bytes(serialized)

        assert restored.name == original.name
        assert restored.extension == original.extension
        assert restored.attributes == original.attributes
        assert restored.first_cluster == original.first_cluster
        assert restored.file_size == original.file_size

    def test_is_free_null(self):
        """Test detection of free entry (never used)."""
        data = bytes(32)  # All zeros
        entry = DirectoryEntry.from_bytes(data)
        assert entry.is_free
        assert entry.is_end

    def test_is_deleted(self):
        """Test detection of deleted entry."""
        data = bytearray(32)
        data[0] = 0xE5  # Deleted marker
        data[1:8] = b"DELETED"
        data[8:11] = b"TXT"

        entry = DirectoryEntry.from_bytes(bytes(data))

        assert entry.is_deleted
        assert entry.is_free
        assert not entry.is_end

    def test_dot_entries(self):
        """Test . and .. entry detection."""
        # Create "." entry
        data = bytearray(32)
        data[0:8] = b".       "
        data[8:11] = b"   "
        data[11] = ATTR_DIRECTORY

        entry = DirectoryEntry.from_bytes(bytes(data))
        assert entry.is_dot_entry

        # Create ".." entry
        data[0:8] = b"..      "
        entry = DirectoryEntry.from_bytes(bytes(data))
        assert entry.is_dot_entry

    def test_attr_string(self):
        """Test attribute string generation."""
        entry = DirectoryEntry(
            name="TEST    ",
            extension="   ",
            attributes=ATTR_READONLY | ATTR_HIDDEN | ATTR_SYSTEM | ATTR_ARCHIVE,
            first_cluster=0,
            file_size=0
        )
        assert entry.attr_string() == "RHSA"

        entry2 = DirectoryEntry(
            name="DIR     ",
            extension="   ",
            attributes=ATTR_DIRECTORY,
            first_cluster=0,
            file_size=0
        )
        assert entry2.attr_string() == "D"


# =============================================================================
# Unit Tests: PhysicalDiskLabel
# =============================================================================

class TestPhysicalDiskLabel:
    """Test PhysicalDiskLabel parsing."""

    def test_from_bytes(self, hard_disk_readonly):
        """Parse physical disk label from real image."""
        assert hard_disk_readonly._physical_label is not None
        label = hard_disk_readonly._physical_label

        # Label type 2 indicates virtual volume label format used at sector 0
        assert label.label_type in (0x0001, 0x0002)
        assert label.device_id == 0x0001
        assert label.sector_size == SECTOR_SIZE
        assert len(label.virtual_volume_addresses) > 0

    def test_virtual_volume_count(self, hard_disk_readonly):
        """Verify multiple partitions are detected."""
        label = hard_disk_readonly._physical_label
        assert len(label.virtual_volume_addresses) >= 1


# =============================================================================
# Unit Tests: VirtualVolumeLabel
# =============================================================================

class TestVirtualVolumeLabel:
    """Test VirtualVolumeLabel parsing."""

    def test_from_bytes(self, hard_disk_readonly):
        """Parse virtual volume label from real image."""
        partition = hard_disk_readonly.get_partition(0)
        vvl = partition.volume_label

        # Label type 1 or 2 are valid virtual volume types
        assert vvl.label_type in (0x0001, 0x0002)
        assert vvl.volume_capacity > 0
        assert vvl.allocation_unit > 0
        assert vvl.num_dir_entries > 0


# =============================================================================
# Unit Tests: Utility Functions
# =============================================================================

class TestValidateFilename:
    """Test filename validation function."""

    def test_valid_names(self):
        """Test valid 8.3 filenames."""
        name, ext = validate_filename("FILE.TXT")
        assert name == "FILE    "
        assert ext == "TXT"

        name, ext = validate_filename("COMMAND.COM")
        assert name == "COMMAND "
        assert ext == "COM"

        name, ext = validate_filename("A.B")
        assert name == "A       "
        assert ext == "B  "

    def test_no_extension(self):
        """Test filenames without extension."""
        name, ext = validate_filename("README")
        assert name == "README  "
        assert ext == "   "

        name, ext = validate_filename("FILE")
        assert name == "FILE    "
        assert ext == "   "

    def test_lowercase_converted(self):
        """Test that lowercase is converted to uppercase."""
        name, ext = validate_filename("test.txt")
        assert name == "TEST    "
        assert ext == "TXT"

    def test_too_long_name(self):
        """Test that names longer than 8 chars are rejected."""
        with pytest.raises(InvalidFilenameError):
            validate_filename("VERYLONGNAME.TXT")

    def test_too_long_extension(self):
        """Test that extensions longer than 3 chars are rejected."""
        with pytest.raises(InvalidFilenameError):
            validate_filename("FILE.TEXT")

    def test_empty_name(self):
        """Test that empty names are rejected."""
        with pytest.raises(InvalidFilenameError):
            validate_filename("")

        with pytest.raises(InvalidFilenameError):
            validate_filename(".TXT")

    def test_invalid_chars(self):
        """Test that invalid characters are rejected."""
        with pytest.raises(InvalidFilenameError):
            validate_filename("FILE<>.TXT")

        with pytest.raises(InvalidFilenameError):
            validate_filename("FILE*.TXT")


class TestParseImagePath:
    """Test image path parsing function."""

    def test_floppy_root(self):
        """Parse floppy path to root."""
        img, part, path = parse_image_path("disk.img:\\")
        assert img == "disk.img"
        assert part is None
        assert path is None or path == ""

    def test_floppy_file(self):
        """Parse floppy path to file."""
        img, part, path = parse_image_path("disk.img:\\FILE.TXT")
        assert img == "disk.img"
        assert part is None
        assert path == "FILE.TXT"

    def test_floppy_subdir(self):
        """Parse floppy path to subdirectory."""
        img, part, path = parse_image_path("disk.img:\\DIR\\FILE.TXT")
        assert img == "disk.img"
        assert part is None
        assert path == "DIR\\FILE.TXT"

    def test_hd_partition_root(self):
        """Parse hard disk partition root path."""
        img, part, path = parse_image_path("vichd.img:0:\\")
        assert img == "vichd.img"
        assert part == 0
        assert path is None or path == ""

    def test_hd_partition_file(self):
        """Parse hard disk partition file path."""
        img, part, path = parse_image_path("vichd.img:0:\\FILE.TXT")
        assert img == "vichd.img"
        assert part == 0
        assert path == "FILE.TXT"

    def test_hd_partition_subdir(self):
        """Parse hard disk partition subdirectory path."""
        img, part, path = parse_image_path("vichd.img:1:\\DIR\\FILE.TXT")
        assert img == "vichd.img"
        assert part == 1
        assert path == "DIR\\FILE.TXT"

    def test_image_only(self):
        """Parse image path without internal path."""
        img, part, path = parse_image_path("disk.img")
        assert img == "disk.img"
        assert part is None
        assert path is None


class TestWildcardMatching:
    """Test wildcard matching functions."""

    def test_has_wildcards(self):
        """Test wildcard detection."""
        assert has_wildcards("*.*")
        assert has_wildcards("*.COM")
        assert has_wildcards("FILE?.TXT")
        assert has_wildcards("*")
        assert not has_wildcards("FILE.TXT")
        assert not has_wildcards("COMMAND.COM")

    def test_star_dot_star(self):
        """Test *.* pattern matching."""
        assert match_filename("*.*", "FILE.TXT")
        assert match_filename("*.*", "A.B")
        assert match_filename("*.*", "LONGNAME.COM")
        assert not match_filename("*.*", "README")  # No extension

    def test_star_only(self):
        """Test * pattern matching (all files)."""
        assert match_filename("*", "FILE.TXT")
        assert match_filename("*", "README")
        assert match_filename("*", "COMMAND.COM")
        assert match_filename("*", "A")

    def test_pattern_match(self):
        """Test specific patterns."""
        assert match_filename("*.COM", "COMMAND.COM")
        assert match_filename("*.COM", "TEST.COM")
        assert not match_filename("*.COM", "FILE.TXT")

        assert match_filename("FILE.*", "FILE.TXT")
        assert match_filename("FILE.*", "FILE.COM")
        assert not match_filename("FILE.*", "OTHER.TXT")

    def test_question_mark(self):
        """Test ? single character wildcard."""
        assert match_filename("FILE?.TXT", "FILE1.TXT")
        assert match_filename("FILE?.TXT", "FILEA.TXT")
        assert not match_filename("FILE?.TXT", "FILE.TXT")
        assert not match_filename("FILE?.TXT", "FILE12.TXT")

    def test_no_match(self):
        """Test non-matching patterns."""
        assert not match_filename("*.EXE", "FILE.COM")
        assert not match_filename("TEST*", "FILE.TXT")


class TestSplitInternalPath:
    """Test internal path splitting."""

    def test_empty_path(self):
        """Test empty path."""
        assert split_internal_path("") == []
        assert split_internal_path(None) == []

    def test_root_path(self):
        """Test root path."""
        assert split_internal_path("\\") == []
        assert split_internal_path("/") == []

    def test_single_component(self):
        """Test single path component."""
        assert split_internal_path("FILE.TXT") == ["FILE.TXT"]
        assert split_internal_path("\\FILE.TXT") == ["FILE.TXT"]

    def test_multiple_components(self):
        """Test multiple path components."""
        assert split_internal_path("DIR\\FILE.TXT") == ["DIR", "FILE.TXT"]
        assert split_internal_path("\\DIR\\SUBDIR\\FILE.TXT") == ["DIR", "SUBDIR", "FILE.TXT"]


# =============================================================================
# Unit Tests: FAT12 Operations
# =============================================================================

class TestFAT12Operations:
    """Test FAT12 entry read/write operations."""

    def test_get_fat_entry_even_cluster(self, floppy_image_readonly):
        """Read FAT entry for even cluster number."""
        # Cluster 2 is the first data cluster
        entry = floppy_image_readonly.get_fat_entry(2)
        # Should be either a valid cluster or EOF
        assert 0 <= entry <= 0xFFF

    def test_get_fat_entry_odd_cluster(self, floppy_image_readonly):
        """Read FAT entry for odd cluster number."""
        entry = floppy_image_readonly.get_fat_entry(3)
        assert 0 <= entry <= 0xFFF

    def test_get_fat_entry_end_of_chain(self, floppy_image_readonly):
        """Detect end-of-chain marker."""
        # Find a file and follow its chain to the end
        entries = floppy_image_readonly.read_root_directory()
        file_entry = None
        for e in entries:
            if not e.is_directory and e.first_cluster > 0:
                file_entry = e
                break

        if file_entry:
            chain = floppy_image_readonly.follow_chain(file_entry.first_cluster)
            if chain:
                last_cluster = chain[-1]
                next_entry = floppy_image_readonly.get_fat_entry(last_cluster)
                # End of chain is 0xFF8-0xFFF
                assert FAT_EOF_MIN <= next_entry <= FAT_EOF_MAX

    def test_set_fat_entry_roundtrip(self, blank_ds_copy):
        """Write and read back a FAT entry."""
        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            test_cluster = 10
            test_value = 0x123

            # Set entry
            disk.set_fat_entry(test_cluster, test_value)

            # Read it back
            result = disk.get_fat_entry(test_cluster)
            assert result == test_value

    def test_follow_chain_single_cluster(self, floppy_image_readonly):
        """Follow a chain of a single cluster."""
        entries = floppy_image_readonly.read_root_directory()

        # Find a small file (single cluster)
        for e in entries:
            if not e.is_directory and 0 < e.file_size <= CLUSTER_SIZE:
                chain = floppy_image_readonly.follow_chain(e.first_cluster)
                assert len(chain) == 1
                return

    def test_follow_chain_multiple_clusters(self, floppy_image_readonly):
        """Follow a chain spanning multiple clusters."""
        entries = floppy_image_readonly.read_root_directory()

        # Find a large file (multiple clusters)
        for e in entries:
            if not e.is_directory and e.file_size > CLUSTER_SIZE:
                chain = floppy_image_readonly.follow_chain(e.first_cluster)
                expected_clusters = (e.file_size + CLUSTER_SIZE - 1) // CLUSTER_SIZE
                assert len(chain) == expected_clusters
                return

    def test_allocate_chain(self, blank_ds_copy):
        """Allocate a chain of clusters."""
        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            # Allocate 3 clusters
            chain = disk.allocate_chain(3)

            assert len(chain) == 3
            # Verify chain is linked
            assert disk.get_fat_entry(chain[0]) == chain[1]
            assert disk.get_fat_entry(chain[1]) == chain[2]
            assert FAT_EOF_MIN <= disk.get_fat_entry(chain[2]) <= FAT_EOF_MAX

    def test_free_chain(self, blank_ds_copy):
        """Free a cluster chain."""
        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            # Allocate then free
            chain = disk.allocate_chain(3)
            disk.free_chain(chain[0])

            # Verify all clusters are free
            for cluster in chain:
                assert disk.get_fat_entry(cluster) == FAT_FREE


# =============================================================================
# Integration Tests: Floppy Disk Operations
# =============================================================================

class TestFloppyDiskOperations:
    """Test floppy disk read/write operations."""

    def test_list_root_directory(self, floppy_image_readonly):
        """List files in root directory."""
        entries = floppy_image_readonly.list_files()
        assert len(entries) > 0

        # Verify known files exist
        names = [e.full_name for e in entries]
        assert "COMMAND.COM" in names

    def test_list_directory_returns_entries(self, floppy_image_readonly):
        """Verify directory listing returns DirectoryEntry objects."""
        entries = floppy_image_readonly.read_root_directory()
        assert all(isinstance(e, DirectoryEntry) for e in entries)

    def test_read_file_content(self, floppy_image_readonly, temp_dir):
        """Read file and verify content matches reference."""
        ref_file = REFERENCE_FILES_DIR / "COMMAND.COM"
        if not ref_file.exists():
            pytest.skip("Reference file not available")

        data = floppy_image_readonly.read_file(["COMMAND.COM"])

        assert data == ref_file.read_bytes()

    def test_read_file_multi_cluster(self, floppy_image_readonly):
        """Read a file spanning multiple clusters."""
        entries = floppy_image_readonly.read_root_directory()

        # Find a large file
        large_file = None
        for e in entries:
            if not e.is_directory and e.file_size > CLUSTER_SIZE:
                large_file = e
                break

        if large_file:
            data = floppy_image_readonly.read_file([large_file.full_name])
            assert len(data) == large_file.file_size

    def test_read_file_verify_reference(self, floppy_image_readonly):
        """Read multiple files and verify against references."""
        test_files = ["COMMAND.COM", "MSDOS.SYS", "CONFIG.SYS"]

        for filename in test_files:
            ref_path = REFERENCE_FILES_DIR / filename
            if not ref_path.exists():
                continue

            data = floppy_image_readonly.read_file([filename])
            assert data == ref_path.read_bytes(), f"Mismatch in {filename}"

    def test_write_new_file_small(self, blank_ds_copy, temp_dir):
        """Write a small file (less than one cluster)."""
        test_data = create_test_data(500)

        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            disk.write_file(["TEST.TXT"], test_data)

        # Read it back
        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["TEST.TXT"])
            assert data == test_data

    def test_write_new_file_large(self, blank_ds_copy):
        """Write a large file spanning multiple clusters."""
        test_data = create_test_data(5000)  # ~2.5 clusters

        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            disk.write_file(["BIGFILE.DAT"], test_data)

        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["BIGFILE.DAT"])
            assert data == test_data

    def test_write_file_overwrites_existing(self, floppy_image_copy):
        """Writing to existing file overwrites it."""
        new_data = b"NEW CONTENT HERE"

        with V9KDiskImage(str(floppy_image_copy), readonly=False) as disk:
            # Write new content to existing file
            disk.write_file(["CONFIG.SYS"], new_data)

        with V9KDiskImage(str(floppy_image_copy), readonly=True) as disk:
            data = disk.read_file(["CONFIG.SYS"])
            assert data == new_data

    def test_delete_file_root(self, floppy_image_copy):
        """Delete a file from root directory."""
        with V9KDiskImage(str(floppy_image_copy), readonly=False) as disk:
            # Verify file exists
            entries_before = disk.list_files()
            names_before = [e.full_name for e in entries_before]
            assert "CONFIG.SYS" in names_before

            # Delete file
            disk.delete_file(["CONFIG.SYS"])

        # Verify deletion
        with V9KDiskImage(str(floppy_image_copy), readonly=True) as disk:
            entries = disk.list_files()
            names = [e.full_name for e in entries]
            assert "CONFIG.SYS" not in names

    def test_delete_nonexistent_file(self, floppy_image_copy):
        """Deleting nonexistent file raises error."""
        with V9KDiskImage(str(floppy_image_copy), readonly=False) as disk:
            with pytest.raises(V9KFileNotFoundError):
                disk.delete_file(["NOFILE.TXT"])

    def test_delete_frees_clusters(self, floppy_image_copy):
        """Verify that deleting a file frees its clusters."""
        with V9KDiskImage(str(floppy_image_copy), readonly=False) as disk:
            # Find a file with known cluster
            entries = disk.list_files()
            target = None
            for e in entries:
                if not e.is_directory and e.first_cluster > 0:
                    target = e
                    break

            if target:
                first_cluster = target.first_cluster
                disk.delete_file([target.full_name])

                # Verify cluster is now free
                assert disk.get_fat_entry(first_cluster) == FAT_FREE

    def test_single_sided_geometry(self):
        """Verify single-sided disk geometry."""
        if not BLANK_SS_IMG.exists():
            pytest.skip("Single-sided blank disk not available")

        with V9KDiskImage(str(BLANK_SS_IMG), readonly=True) as disk:
            assert not disk._double_sided
            assert disk._fat_sectors == 1
            assert disk._dir_start == 3
            assert disk._data_start == 11

    def test_double_sided_geometry(self, floppy_image_readonly):
        """Verify double-sided disk geometry."""
        assert floppy_image_readonly._double_sided
        assert floppy_image_readonly._fat_sectors == 2
        assert floppy_image_readonly._dir_start == 5
        assert floppy_image_readonly._data_start == 13


class TestFloppyWildcardCopy:
    """Test wildcard copy operations."""

    def test_copy_wildcard_star_dot_star(self, floppy_image_readonly, temp_dir):
        """Copy all files with *.* pattern."""
        entries = floppy_image_readonly.read_root_directory()
        file_count = sum(1 for e in entries if not e.is_directory and not e.is_dot_entry)

        matches = floppy_image_readonly.find_matching_files(["*.*"])
        assert len(matches) >= 1  # At least some files

    def test_copy_wildcard_pattern(self, floppy_image_readonly, temp_dir):
        """Copy files matching *.COM pattern."""
        matches = floppy_image_readonly.find_matching_files(["*.COM"])

        # Verify all matches have .COM extension
        for path, entry in matches:
            assert entry.extension.strip() == "COM"


# =============================================================================
# Integration Tests: Hard Disk Operations
# =============================================================================

class TestHardDiskOperations:
    """Test hard disk partition operations."""

    def test_list_partitions(self, hard_disk_readonly):
        """List all partitions on hard disk."""
        partitions = hard_disk_readonly.list_partitions()
        assert len(partitions) >= 1

        # Verify partition info
        for p in partitions:
            assert 'index' in p
            assert 'name' in p
            assert 'capacity' in p

    def test_partition_count(self, hard_disk_readonly):
        """Verify partition count property."""
        count = hard_disk_readonly.partition_count
        partitions = hard_disk_readonly.list_partitions()
        assert count == len(partitions)

    def test_get_partition(self, hard_disk_readonly):
        """Get a specific partition."""
        partition = hard_disk_readonly.get_partition(0)
        assert partition is not None
        assert isinstance(partition, V9KPartition)

    def test_invalid_partition_number(self, hard_disk_readonly):
        """Accessing invalid partition raises error."""
        with pytest.raises(InvalidPartitionError):
            hard_disk_readonly.get_partition(999)

        with pytest.raises(InvalidPartitionError):
            hard_disk_readonly.get_partition(-1)

    def test_list_partition_root(self, hard_disk_readonly):
        """List root directory of partition."""
        partition = hard_disk_readonly.get_partition(0)
        entries = partition.list_files()
        assert isinstance(entries, list)

    def test_read_file_from_partition(self, hard_disk_readonly):
        """Read a file from partition."""
        partition = hard_disk_readonly.get_partition(0)
        entries = partition.list_files()

        # Find a file to read
        file_entry = None
        for e in entries:
            if not e.is_directory and e.file_size > 0:
                file_entry = e
                break

        if file_entry:
            data = partition.read_file([file_entry.full_name])
            assert len(data) == file_entry.file_size


class TestImageTypeDetection:
    """Test automatic image type detection."""

    def test_detect_floppy(self):
        """Detect floppy disk image."""
        result = detect_image_type(str(FLOPPY_DISK_IMG))
        assert result == 'floppy'

    def test_detect_hard_disk(self):
        """Detect hard disk image."""
        result = detect_image_type(str(HARD_DISK_IMG))
        assert result == 'harddisk'

    def test_detect_blank_floppy(self):
        """Detect blank floppy image."""
        result = detect_image_type(str(BLANK_DS_IMG))
        assert result == 'floppy'


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_file(self, blank_ds_copy):
        """Handle 0-byte file."""
        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            disk.write_file(["EMPTY.TXT"], b"")

        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["EMPTY.TXT"])
            assert data == b""
            assert len(data) == 0

    def test_file_exact_cluster_boundary(self, blank_ds_copy):
        """File exactly at cluster boundary (2048 bytes)."""
        test_data = create_test_data(CLUSTER_SIZE)

        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            disk.write_file(["EXACT.DAT"], test_data)

        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["EXACT.DAT"])
            assert len(data) == CLUSTER_SIZE
            assert data == test_data

    def test_file_one_byte_over_cluster(self, blank_ds_copy):
        """File one byte over cluster boundary."""
        test_data = create_test_data(CLUSTER_SIZE + 1)

        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            disk.write_file(["OVER.DAT"], test_data)

        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["OVER.DAT"])
            assert len(data) == CLUSTER_SIZE + 1
            assert data == test_data

    def test_max_filename_length(self, blank_ds_copy):
        """File with maximum length 8.3 name."""
        test_data = b"test content"

        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            disk.write_file(["ABCDEFGH.XYZ"], test_data)

        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["ABCDEFGH.XYZ"])
            assert data == test_data

    def test_file_no_extension(self, blank_ds_copy):
        """File without extension."""
        test_data = b"no extension file"

        with V9KDiskImage(str(blank_ds_copy), readonly=False) as disk:
            disk.write_file(["README"], test_data)

        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["README"])
            assert data == test_data

    def test_open_nonexistent_image(self, temp_dir):
        """Opening nonexistent image raises error."""
        with pytest.raises(DiskError):
            V9KDiskImage(str(temp_dir / "nonexistent.img"), readonly=True)

    def test_readonly_write_fails(self, floppy_image_readonly):
        """Writing to readonly image fails."""
        with pytest.raises(DiskError):
            floppy_image_readonly.write_sector(0, bytes(512))


# =============================================================================
# Output Formatter Tests
# =============================================================================

class TestOutputFormatter:
    """Test OutputFormatter class."""

    def test_success_text_mode(self, capsys):
        """Test success output in text mode."""
        formatter = OutputFormatter(json_mode=False)
        formatter.success("Operation completed")
        captured = capsys.readouterr()
        assert "Operation completed" in captured.out

    def test_success_json_mode(self, capsys):
        """Test success output in JSON mode."""
        import json as json_lib
        formatter = OutputFormatter(json_mode=True)
        formatter.success("Done", count=5)
        captured = capsys.readouterr()
        output = json_lib.loads(captured.out)
        assert output["status"] == "success"
        assert output["message"] == "Done"
        assert output["count"] == 5

    def test_error_text_mode(self, capsys):
        """Test error output in text mode."""
        formatter = OutputFormatter(json_mode=False)
        formatter.error("Something went wrong")
        captured = capsys.readouterr()
        assert "Error:" in captured.err
        assert "Something went wrong" in captured.err

    def test_error_json_mode(self, capsys):
        """Test error output in JSON mode."""
        import json as json_lib
        formatter = OutputFormatter(json_mode=True)
        formatter.error("Failed")
        captured = capsys.readouterr()
        output = json_lib.loads(captured.out)
        assert output["status"] == "error"
        assert output["message"] == "Failed"


# =============================================================================
# CLI Tests
# =============================================================================

class TestCLI:
    """Test command-line interface."""

    def test_cli_list_floppy(self, capsys):
        """Test list command on floppy."""
        class Args:
            path = str(FLOPPY_DISK_IMG)

        formatter = OutputFormatter(json_mode=False)
        result = cmd_list(Args(), formatter)
        assert result == 0

        captured = capsys.readouterr()
        assert "Directory of" in captured.out

    def test_cli_list_json(self, capsys):
        """Test list command with JSON output."""
        import json as json_lib

        class Args:
            path = str(FLOPPY_DISK_IMG)

        formatter = OutputFormatter(json_mode=True)
        result = cmd_list(Args(), formatter)
        assert result == 0

        captured = capsys.readouterr()
        output = json_lib.loads(captured.out)
        assert output["status"] == "success"
        assert "files" in output

    def test_cli_list_hard_disk_partitions(self, capsys):
        """Test list command shows partitions for hard disk."""
        class Args:
            path = str(HARD_DISK_IMG)

        formatter = OutputFormatter(json_mode=False)
        result = cmd_list(Args(), formatter)
        assert result == 0

        captured = capsys.readouterr()
        assert "Partitions" in captured.out or "partition" in captured.out.lower()

    def test_cli_copy_from_image(self, temp_dir):
        """Test copy command from image to filesystem."""
        dest_file = temp_dir / "COMMAND.COM"

        class Args:
            source = f"{FLOPPY_DISK_IMG}:\\COMMAND.COM"
            dest = str(dest_file)
            recursive = False

        formatter = OutputFormatter(json_mode=False)
        result = cmd_copy(Args(), formatter)
        assert result == 0
        assert dest_file.exists()

    def test_cli_copy_to_image(self, blank_ds_copy, temp_dir):
        """Test copy command from filesystem to image."""
        # Create source file
        source_file = temp_dir / "TEST.TXT"
        source_file.write_bytes(b"Test content")

        class Args:
            source = str(source_file)
            dest = f"{blank_ds_copy}:\\TEST.TXT"
            recursive = False

        formatter = OutputFormatter(json_mode=False)
        result = cmd_copy(Args(), formatter)
        assert result == 0

        # Verify file was written
        with V9KDiskImage(str(blank_ds_copy), readonly=True) as disk:
            data = disk.read_file(["TEST.TXT"])
            assert data == b"Test content"

    def test_cli_delete(self, floppy_image_copy):
        """Test delete command."""
        class Args:
            path = f"{floppy_image_copy}:\\CONFIG.SYS"

        formatter = OutputFormatter(json_mode=False)
        result = cmd_delete(Args(), formatter)
        assert result == 0

        # Verify deletion
        with V9KDiskImage(str(floppy_image_copy), readonly=True) as disk:
            entries = disk.list_files()
            names = [e.full_name for e in entries]
            assert "CONFIG.SYS" not in names


# =============================================================================
# Context Manager Tests
# =============================================================================

class TestContextManagers:
    """Test context manager behavior."""

    def test_floppy_context_manager(self):
        """Test V9KDiskImage as context manager."""
        with V9KDiskImage(str(FLOPPY_DISK_IMG), readonly=True) as disk:
            entries = disk.list_files()
            assert len(entries) > 0
        # File should be closed now

    def test_hard_disk_context_manager(self):
        """Test V9KHardDiskImage as context manager."""
        with V9KHardDiskImage(str(HARD_DISK_IMG), readonly=True) as disk:
            partitions = disk.list_partitions()
            assert len(partitions) > 0


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
