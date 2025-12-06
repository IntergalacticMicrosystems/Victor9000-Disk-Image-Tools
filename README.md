## Project Overview
Victor 9000 disk image utility written in Python 3.12+.
Supports FAT12 filesystem operations including subdirectories.
Supports both floppy and hard disk images with multiple partitions.

## Usage

### List files (Floppy)
```bash
v9k_image_util.py list disk.img                    # List root directory
v9k_image_util.py list disk.img:\SUBDIR            # List subdirectory
v9k_image_util.py list disk.img --json             # JSON output
```

### List files (Hard Disk)
```bash
v9k_image_util.py list vichd.img                   # List partitions
v9k_image_util.py list vichd.img:0:\               # List root of partition 0
v9k_image_util.py list vichd.img:1:\SUBDIR         # List subdirectory on partition 1
v9k_image_util.py list vichd.img --json            # JSON output (partitions)
```

### Copy from image (Floppy)
```bash
v9k_image_util.py copy disk.img:\COMMAND.COM c:\temp\command.com    # Single file
v9k_image_util.py copy disk.img:\*.* c:\temp\                       # Wildcard copy
v9k_image_util.py copy disk.img:\* c:\temp\                         # All files (incl. no extension)
v9k_image_util.py copy disk.img:\*.COM c:\temp\                     # Pattern match
v9k_image_util.py copy disk.img:\* c:\temp\ -r                      # Recursive with subdirs
```

### Copy from image (Hard Disk)
```bash
v9k_image_util.py copy vichd.img:0:\COMMAND.COM c:\temp\            # From partition 0
v9k_image_util.py copy vichd.img:1:\*.* c:\temp\                    # Wildcard from partition 1
v9k_image_util.py copy vichd.img:2:\* c:\temp\ -r                   # Recursive from partition 2
```

### Copy to image
```bash
v9k_image_util.py copy c:\temp\file.txt disk.img:\FILE.TXT          # Floppy
v9k_image_util.py copy c:\temp\file.txt vichd.img:0:\FILE.TXT       # Hard disk partition 0
```

### Delete file
```bash
v9k_image_util.py delete disk.img:\COMMAND.COM                      # Floppy
v9k_image_util.py delete vichd.img:0:\COMMAND.COM                   # Hard disk partition 0
```

### Options
- `--json` - Output in JSON format (available for all commands)
- `-r, --recursive` - Copy subdirectories recursively (copy command)

## Features
- Copy files from image (with wildcard support)
- Copy files to image
- Delete files on image
- List directory contents
- Full subdirectory navigation
- JSON output mode
- 8.3 filename validation (no LFN support)
- Hard disk support with multiple partitions

## Technical Notes

### Floppy Disks
- Victor 9000 uses 4 sectors per cluster (2048 bytes)
- FAT12 filesystem with 12-bit cluster entries
- Disk geometry detected from boot sector flags (bit 0 = double-sided)
- Double-sided: FAT at sectors 1-4, directory at 5-12, data at 13+
- Single-sided: FAT at sectors 1-2, directory at 3-10, data at 11+

### Hard Disks
- Physical disk label at sector 0 with virtual volume list
- Each partition has its own virtual volume label
- Variable sectors per cluster (typically 64 sectors = 32KB)
- FAT12 with size based on partition capacity
- Image type auto-detected (size >2MB or label structure)

## Key Files
- `v9k_image_util.py` - Main utility implementation
- `victor_boot_sector_layout.md` - Boot sector structure reference
- `victor_floppy_sector_layout.md` - Sector layout reference
- `victor_9000_floppy_disk_notes.md` - More notes on floppy format
- `fat12_floppy_format.md` - FAT12 filesystem reference
- `victor_hard_drive_layout.md` - Victor hard disk info
- `victor_9000_hard_disk_format.md` - Victor hard disk info

<!--
IGNORE THESE, FOR TESTING
- `example_disks/vichd.img` - Victor hard disk test file

## Key Directories
- `example_disks/` - Example disk images for testing
- `example_disks/files/` - Reference files extracted from disk.img
- `example_disks/vichd/` - Reference files extracted from vichd.img
-->
