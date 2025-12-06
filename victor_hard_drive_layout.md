## 2. Victor Disk Label Primer

The Victor 9000 stores its hard-disk topology in the first two sectors of the drive. Appendix K of the *Victor 9000 Hardware Reference Manual* documents the layout in detail (see [bitsavers.org/pdf/victor/victor9000/Victor_9000_Hardware_Reference_Rev_0_19831005.pdf](https://bitsavers.org/pdf/victor/victor9000/Victor_9000_Hardware_Reference_Rev_0_19831005.pdf)).

At a high level the label contains:

- **Header fields**
  - `label_type` — revision flags (bit 0 = qualified, bit 1 = MS-DOS revision); MS-DOS requires bit 1 set.
  - `device_id` — identifies the controller/drive family.
  - `serial_number` — 16 bytes of ASCII.
  - `sector_size` — always 512 for Victor hard disks.
  - `ipl_vector` — Initial Program Load [IPL] vector comprises (`disk_address`, `load_address`, `load_length`, `code_entry`) copied from the primary boot volume. These fields identify the boot loader the machine should read from disk before handing control to MS‑DOS:
    - `disk_address` – the logical sector where the boot image begins.
    - `load_address` – the paragraph in RAM where it should be loaded (0 means “load at top of memory”).
    - `load_length` – size of the boot image in paragraphs.
    - `code_entry` – the far address to jump to after loading.
  - `primary_boot_volume` — index of the virtual volume whose label contains the system IPL data.
- **Controller parameters**
  16 bytes describing the cylinder/head geometry, reduced-current and write-precompensation tracks, ECC burst length, option bits, interleave, and six spare bytes.
- **Available media list**
  - `region_count`
  - For each region: a `<physical_address, block_count>` describing the raw usable spans reported by the formatter (before considering bad-track replacement).
- **Working media list**
  Mirrors the available list but reflects the working regions currently in service. Each entry becomes a “band” the boot ROM’s `hd_read` walks through; therefore each `block_count` must stay below 65 536 sectors to avoid the 16-bit overflow in the ROM.
- **Virtual volume list**
  - `volume_count`
  - For each volume: the logical sector address of the virtual volume label. Those per-volume labels contain the FAT geometry, cluster size, root directory length, and optional drive-letter assignments. Together the addresses in this list define the partition structure that tools such as `HDSETUP` present.

Understanding these fields helps explain why the region limits in the next section matter: they map directly to the working-media entries consumed by the boot firmware.

