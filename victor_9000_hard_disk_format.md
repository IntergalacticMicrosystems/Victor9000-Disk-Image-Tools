# Victor 9000 Hard Disk Format Reference

This document describes the hard disk and floppy disk format used by the Victor 9000 computer system, based on the Victor 9000 Hardware Reference Manual (Rev 0, October 5, 1983).

## Overview

The Victor 9000 uses a hierarchical disk label system with physical disk labels and virtual volume labels. This allows multiple virtual volumes to exist on a single physical hard disk.

## Disk Parameters Comparison

| Parameter | Hard Disk (C:) | Floppy (A:) |
|-----------|----------------|-------------|
| Drive Number | 2 | - |
| Unit | 0 | - |
| Sector Size | 512 bytes | 512 bytes |
| Cluster Size | 16 sectors | 4 sectors |
| Media Description Byte | 02h | 01h |
| Available Space | 2497 clusters (20,455,424 bytes) | 594 clusters (1,215,512 bytes) |
| Reserved Sectors | 1 | 1 |
| File Allocation Tables | 2 | 2 |
| Sectors per FAT | 8 | 2 |
| Directory Sectors | 20 | 8 |
| Max Directory Entries | 312 | 128 |

## MS-DOS Disk Layout

MS-DOS allocates space on single-sided (SS) and double-sided (DS) diskettes as follows:

### Single-Sided Diskette (SS)

| Location | Contents |
|----------|----------|
| Track 0, Sector 0 | Disk Label |
| Sectors 1-2 | Two copies of the FAT (2 sectors per FAT) |
| Sectors 3-10 | Directory |
| Sectors 11+ | Data Region |

### Double-Sided Diskette (DS)

| Location | Contents |
|----------|----------|
| Track 0, Sector 0 | Disk Label |
| Sectors 1-4 | Two copies of the FAT (2 sectors per FAT) |
| Sectors 5-12 | Directory |
| Sectors 13+ | Data Region |

---

## Hard Disk Label Format

The hard disk label is located in the first sector and contains the following fields:

### Label Header

| Field Name | Data Type | Contents/Description |
|------------|-----------|---------------------|
| Label_Type | WORD | `0000h` = unqualified, `0001h` = current revision |
| Device_ID | WORD | `0001h` = current revision. Identifies controller/drive compatibility |
| Serial_Number | BYTE[16] | ASCII serial number of the unit |
| Sector_Size | WORD | 512 bytes (physical atomic unit of storage) |

### Initial Program Load (IPL) Vector

The IPL vector identifies the boot program and its location on disk:

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Disk_Address | DWORD | Logical disk address of the boot program image |
| Load_Address | WORD | Paragraph number where boot program loads (0 = highest RAM) |
| Load_Length | WORD | Length of boot program in paragraphs |
| Code_Entry | PTR | Memory address of boot program entry point (segment 0 = use loaded segment) |

### Boot Configuration

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Primary_Boot_Volume | WORD | Virtual volume number containing IPL vector and configuration |

### Controller Parameters (for Tandem TM603SE)

| Field Name | Data Type | Value | Description |
|------------|-----------|-------|-------------|
| # Cylinders (high) | BYTE | 00h | High byte of cylinder count |
| # Cylinders (low) | BYTE | E6h | Low byte (= 230 cylinders total) |
| # Heads | BYTE | 06h | Number of heads (= 6) |
| 1st Reduced Current Cyl (high) | BYTE | 00h | - |
| 1st Reduced Current Cyl (low) | BYTE | 80h | = 128 |
| 1st Write Precomp Cyl (high) | BYTE | 00h | - |
| 1st Write Precomp Cyl (low) | BYTE | 80h | = 128 |
| ECC Data Burst | BYTE | 0Bh | = 11 |
| Options | BYTE | 02h | = 2 |
| Interleave | BYTE | 05h | = 5 (note: 0 also means 5) |
| Spares | BYTE[6] | 00h | Reserved |

### Available Media List

Defines permanent useable areas of the disk, derived from format function of HDSETUP utility:

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Region_Count | BYTE | Number of regions |
| Region_Descr | (variable) | Variable length based on region count |
| Region_PA | DWORD | Physical address of region |
| Region_Size | DWORD | Number of physical blocks in region |

### Working Media List

Defines working areas of the disk, derived from Available Media List and HDSETUP format function:

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Region_Count | BYTE | Number of regions |
| Region_Descr | (variable) | Variable length based on region count |
| Region_PA | DWORD | Physical disk address of region |
| Region_Size | DWORD | Number of physical blocks in region |

### Virtual Volume List

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Volume_Count | BYTE | Number of virtual volumes |
| Volume_Address | DWORD | Logical address of each virtual volume label |

---

## Virtual Volume Label Format

The Virtual Volume Label provides information on the structure of each virtual volume. The operating system references this label, while the HDSETUP utility creates and manages it.

### Volume Header

| Field Name | Data Type | Contents/Description |
|------------|-----------|---------------------|
| Label_Type | WORD | `0000h` = null. Defines operating environment type for type checking |
| Volume_Name | BYTE[16] | ASCII name of the virtual volume (user-defined) |

### Volume IPL Vector

Used to generate the IPL vector on the drive label when configuring the primary boot volume:

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Disk_Address | DWORD | Virtual disk address of the boot program image |
| Load_Address | WORD | Paragraph address for boot program load (0 = highest RAM) |
| Load_Length | WORD | Length of boot program in paragraphs |
| Code_Entry | PTR | Memory address of boot program entry point (segment 0 = use loaded segment) |

### Volume Parameters

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Volume_Capacity | DWORD | Number of actual blocks comprising the virtual volume |
| Data_Start | DWORD | Virtual address offset (in blocks) to start of data space |
| Host_Block_Size | WORD | Atomic unit for host data transfer (MS-DOS = 512 bytes) |
| Allocation_Unit | WORD | Number of physical blocks per allocation unit (cluster). Used for disk parameter tables |
| Number_Of_Directory_Entries | WORD | Number of entries in the host's directory. Used for disk parameter tables |
| Reserved | BYTE[16] | Future expansion - set to nulls |

### Configuration Information

Used to map logical drives to virtual volumes at boot time:

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| Assignment_Count | BYTE | Number of assignment mappings |
| Assignment | (variable) | Variable length based on assignment count |
| Device_Unit | WORD | Physical unit number |
| Volume_Index | WORD | Index into virtual volume list |

---

## Key Concepts

### Label Types
- **0000h (unqualified)**: Label has not been properly initialized
- **0001h (current revision)**: Valid, current format label

### Device ID
Used to identify compatible controller/drive combinations. Allows the system to verify hardware compatibility.

### Virtual Volumes
The Victor 9000 supports multiple virtual volumes on a single physical hard disk. Each virtual volume has its own label and can be assigned to a logical drive letter. This provides flexibility in disk organization and multi-boot configurations.

### IPL (Initial Program Load)
The boot process uses the IPL vector to locate and load the operating system. The IPL vector in the virtual volume label is used to populate the drive label when setting up a bootable volume.

### HDSETUP Utility
The HDSETUP utility is used to:
- Format the hard disk
- Create and manage virtual volumes
- Configure boot parameters
- Generate the IPL vectors

---

## Error Diagnosis Notes

The DP101 diagnostic program may report the following errors related to disk format:
- "Error in number of sectors per cluster"
- "Error in bytes per sector"  
- "Illegal number of FATs: (0 on floppy) (32 on hard disk)"
- "The data in the boot area is not the same as returned by DOS function 54"

Note: DOS function 54h returns the verify status in AL (not disk parameters). These errors typically indicate a mismatch between the boot sector data and what DOS expects.

---

## Source

Victor 9000 Hardware Reference Manual, Revision 0, October 5, 1983.
