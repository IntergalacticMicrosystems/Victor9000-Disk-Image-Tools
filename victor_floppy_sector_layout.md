
## Victor 9000 Floppy Sector Layout

512-byte sectors

# Single-sided DOS disks - sector offset
0 boot sector
1 FAT copy 1
2 FAT copy 2
3-10 Directory
11-1224 Data

# Double-sided DOS disks - sector offset
0 boot sector
1-2 FAT copy 1
3-4 FAT copy 2
5-12 Directory
13-2390 Data