
## Victor 9000 Boot Sector Layout

    Track 0 Sector 0

    Byte
    Offset         Name                Description
    0              System disc ID      literally, ff,00h for a system disc
    2              Load address        paragraph to load booted program at. If zero then boot loads in high memory.
    4              Length              paragraph count to load.
    6              Entry offset        I.P. value for transfer of control.
    8              Entry segment       C.S. value for transfer of control.
    10             I.D.                disc identifier.
    18             Part number         system identifier - displayed by early versions of boot.
    26             Sector size         byte count for sectors.
    28             Data start          first data sector on disc (absolute sectors).
    30             Boot start          first absolute sector of program for boot to load at 'load address' for 'length' paragraphs.
    32             Flags               indicators: bit meaning, 15-12 interleave factor (0-15), 0 = 0=single sided, 1=double sided
    34             Disc type           0x00 = CP/M, 0x01 = MS-DOS,  0x10 = MS-DOS 3.1
    35             Reserved
    38             Speed table         information for speed control proc.
    56             Zone table          high track for each zone.
    71             Sector/track        sectors per track for each zone.
