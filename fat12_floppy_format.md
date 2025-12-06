# DOS FAT12 Floppy Disk Format Reference

## Overview

FAT12 is a file system developed by Microsoft for floppy disks. The "12" refers to the 12-bit entries used in the File Allocation Table. It supports volumes up to 16 MB and was the standard for DOS floppy disks.

## File Allocation Table (FAT)

### FAT Structure

The FAT is an array of 12-bit entries that map the cluster chain for each file. Each entry corresponds to a cluster in the data area.

### FAT Entry Values

| Value | Meaning |
|-------|---------|
| 0x000 | Free cluster |
| 0x001 | Reserved |
| 0x002 - 0xFEF | Next cluster in chain |
| 0xFF0 - 0xFF6 | Reserved |
| 0xFF7 | Bad cluster |
| 0xFF8 - 0xFFF | End of chain (EOF) |

### FAT12 Entry Encoding

FAT12 entries are 12 bits (1.5 bytes), packed as follows:

For two consecutive entries at positions N and N+1:
- Entry N uses: byte[0] and low nibble of byte[1]
- Entry N+1 uses: high nibble of byte[1] and byte[2]

```
Bytes:    [  byte 0  ] [  byte 1  ] [  byte 2  ]
Bits:     7654 3210    7654 3210    7654 3210
Entry N:  <--- all 8 bits --><low 4>
Entry N+1:              <hi 4><--- all 8 bits --->
```

### Reading FAT12 Entries (Algorithm)

```
offset = cluster + (cluster / 2)   // 1.5 bytes per entry
value = read_16bit_word(FAT + offset)

if (cluster is even):
    entry = value & 0x0FFF
else:
    entry = value >> 4
```

### Writing FAT12 Entries (Algorithm)

```
offset = cluster + (cluster / 2)
value = read_16bit_word(FAT + offset)

if (cluster is even):
    value = (value & 0xF000) | (new_entry & 0x0FFF)
else:
    value = (value & 0x000F) | (new_entry << 4)

write_16bit_word(FAT + offset, value)
```

### First Two FAT Entries

- Entry 0: Media descriptor byte (same as BPB) with high bits set to 0xFF (e.g., 0xFF0 for 1.44MB)
- Entry 1: End-of-chain marker (0xFFF), may contain dirty/error flags

## Root Directory

### Location

```
root_dir_start = RsvdSecCnt + (NumFATs * FATSz16)
root_dir_sectors = (RootEntCnt * 32 + BytsPerSec - 1) / BytsPerSec
```

For 1.44 MB floppy: sectors 19-32 (14 sectors, 224 entries)

### Directory Entry Structure (32 bytes)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0x00 | 8 | Name | File name (padded with spaces) |
| 0x08 | 3 | Ext | File extension (padded with spaces) |
| 0x0B | 1 | Attr | File attributes |
| 0x0C | 1 | NTRes | Reserved for Windows NT |
| 0x0D | 1 | CrtTimeTenth | Creation time (tenths of second, 0-199) |
| 0x0E | 2 | CrtTime | Creation time |
| 0x10 | 2 | CrtDate | Creation date |
| 0x12 | 2 | LstAccDate | Last access date |
| 0x14 | 2 | FstClusHI | High word of first cluster (0 for FAT12) |
| 0x16 | 2 | WrtTime | Last write time |
| 0x18 | 2 | WrtDate | Last write date |
| 0x1A | 2 | FstClusLO | Low word of first cluster |
| 0x1C | 4 | FileSize | File size in bytes |

### File Attributes (Byte at offset 0x0B)

| Bit | Mask | Attribute |
|-----|------|-----------|
| 0 | 0x01 | Read-only |
| 1 | 0x02 | Hidden |
| 2 | 0x04 | System |
| 3 | 0x08 | Volume label |
| 4 | 0x10 | Subdirectory |
| 5 | 0x20 | Archive |
| 6-7 | 0xC0 | Reserved |

Special combinations:
- 0x0F = Long File Name entry (LFN)
- 0x08 = Volume label (only in root directory)

### File Name Rules (8.3 Format)

- Name: 8 characters, uppercase A-Z, 0-9, and special chars: ! # $ % & ' ( ) - @ ^ _ ` { } ~
- Extension: 3 characters, same character set
- Padded with spaces (0x20)
- First byte special values:
  - 0x00 = Entry is free and no entries follow
  - 0xE5 = Entry is deleted/free
  - 0x05 = First character is actually 0xE5 (escaped)
  - 0x2E = Dot entry ("." or "..")

### Date Format (16-bit)

```
Bits 15-9: Year (0-127, relative to 1980)
Bits 8-5:  Month (1-12)
Bits 4-0:  Day (1-31)
```

### Time Format (16-bit)

```
Bits 15-11: Hours (0-23)
Bits 10-5:  Minutes (0-59)
Bits 4-0:   Seconds/2 (0-29, representing 0-58 seconds)
```

## Data Area

### Cluster to Sector Conversion

```
first_sector_of_cluster = data_start + (cluster - 2) * SecPerClus
```

Note: Clusters are numbered starting from 2 (0 and 1 are reserved in the FAT).

### Reading a File

1. Get first cluster number from directory entry (FstClusLO)
2. Calculate first sector: `data_start + (cluster - 2) * SecPerClus`
3. Read `SecPerClus` sectors
4. Look up next cluster in FAT
5. If next cluster >= 0xFF8, file is complete
6. Otherwise, repeat from step 2 with the new cluster number
7. For the last cluster, only read up to FileSize bytes

## Subdirectories

- Subdirectory entries have attribute 0x10
- FstClusLO points to first cluster containing directory entries
- Directory data is a sequence of 32-byte entries (same format as root)
- First two entries are always "." (self) and ".." (parent)
- Subdirectories can grow dynamically (unlike fixed-size root directory)
- FileSize field is 0 for directories

## Common Operations

### Format a Floppy

1. Write boot sector with BPB at sector 0
2. Initialize FAT1 and FAT2 (first two entries set, rest zeroed)
3. Zero out root directory area
4. Optionally write volume label in root directory

### Create a File

1. Find free entry in directory
2. Find free clusters in FAT for file data
3. Write file data to clusters
4. Update FAT entries to form cluster chain
5. Write directory entry with name, attributes, size, first cluster

### Delete a File

1. Mark first byte of directory entry as 0xE5
2. Mark all clusters in chain as 0x000 (free) in FAT
3. (Data remains on disk until overwritten)

## Byte Order

FAT12 uses little-endian byte order for all multi-byte values.

## References

- Microsoft FAT Specification (fatgen103.pdf)
- ECMA-107: Volume and File Structure of Disk Cartridges for Information Interchange
- IBM PC DOS Technical Reference
