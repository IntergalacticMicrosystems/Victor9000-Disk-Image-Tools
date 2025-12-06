# Victor 9000 Disk Image Technical Notes

Notes compiled while developing the v9k_image_util.py utility.

## Key Discovery: 4 Sectors Per Cluster

The most significant finding was that Victor 9000 DOS uses **4 sectors per cluster** (2048 bytes), not 1 sector as initially assumed from standard FAT12 documentation.

This was discovered when extracting COMMAND.COM:
- Directory entry showed file size: 26,912 bytes
- FAT chain contained only 14 clusters
- With 1 sector/cluster: 14 × 512 = 7,168 bytes (wrong!)
- With 4 sectors/cluster: 14 × 2048 = 28,672 bytes (correct, with padding)

The 4-sector cluster size makes sense given:
- Double-sided disk has 2,378 data sectors
- FAT12 with 1024 bytes can address ~680 clusters
- 2,378 ÷ 4 = 594 clusters (fits in FAT)

## Boot Sector Differences

Victor 9000 does **not** use the standard IBM PC BIOS Parameter Block (BPB). Instead, it has its own boot sector format:

| Offset | Field | Notes |
|--------|-------|-------|
| 0-1 | System ID | 0xFF00 for system disks |
| 26-27 | Sector size | Always 512 |
| 28-29 | Data start | Often 0, requiring default calculation |
| 32-33 | Flags | Bit 0 = double-sided |
| 34 | Disc type | 0x10 = MS-DOS 3.1 |

The `data_start` field at offset 28 is often 0 in disk images, requiring the utility to calculate the correct value based on whether the disk is single or double-sided.

## Disk Geometry

### Double-Sided DOS Disks (1.2 MB)
```
Sector 0:      Boot sector
Sectors 1-2:   FAT copy 1 (2 sectors = 1024 bytes)
Sectors 3-4:   FAT copy 2
Sectors 5-12:  Root directory (8 sectors = 128 entries)
Sectors 13+:   Data area
```

### Single-Sided DOS Disks (600 KB)
```
Sector 0:      Boot sector
Sector 1:      FAT copy 1
Sector 2:      FAT copy 2
Sectors 3-10:  Root directory (8 sectors = 128 entries)
Sectors 11+:   Data area
```

## FAT12 Implementation

Standard FAT12 encoding works correctly:
- 12-bit entries packed as 1.5 bytes each
- Even clusters: low 12 bits of 16-bit word
- Odd clusters: high 12 bits of 16-bit word

```python
offset = cluster + (cluster // 2)
word = fat[offset] | (fat[offset + 1] << 8)
entry = (word & 0x0FFF) if cluster % 2 == 0 else (word >> 4)
```

## Directory Entries

Standard 32-byte FAT directory entries are used:
- 8.3 filename format (8 bytes name, 3 bytes extension)
- Space-padded (0x20)
- First byte 0xE5 = deleted, 0x00 = end of directory
- Attribute 0x10 = subdirectory

Subdirectories work identically to standard FAT12:
- First cluster points to directory data
- Contains "." and ".." entries
- Can span multiple clusters (unlike fixed-size root)

## Character Encoding

Filenames use ASCII/Latin-1 encoding. Some disk images contain entries with high-bit characters that require `latin-1` decoding rather than strict ASCII to avoid errors.

## Files Without Extensions

Victor 9000 DOS supports files without extensions (e.g., `XH`). The wildcard pattern `*.*` does NOT match these files (requires a dot), but `*` alone matches all files including extensionless ones.

## Timestamps

Standard FAT date/time format is used:
- Date: bits 15-9 = year (since 1980), 8-5 = month, 4-0 = day
- Time: bits 15-11 = hour, 10-5 = minute, 4-0 = seconds/2

## Compatibility Notes

- Disk images are raw sector dumps (no header)
- 512-byte sectors throughout
- Little-endian byte order for all multi-byte values
- Two FAT copies must be kept in sync when writing

## Test Observations

Tested with actual Victor 9000 disk images:
- `dos31-ds.img`: Double-sided DOS 3.1 system disk
- `disk.img`: Double-sided disk with 68 files totaling ~1 MB
- All 68 files extracted and verified byte-for-byte against known good copies

## References

- Victor 9000 Technical Reference Manual
- Microsoft FAT12 Specification
- Reverse engineering of actual disk images
