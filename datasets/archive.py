"""Strict seekable ZIP/ZIP64 preflight and bounded independent decompression.

No ZipFile is constructed from unbounded central-directory declarations. No
member name becomes a filesystem path. SFX/padding/digital-signature extensions
are intentionally unsupported rather than silently accepting hidden regions.
"""
from __future__ import annotations

from dataclasses import dataclass
import stat
import struct
import unicodedata
import zlib

from datasets.limits import Limits, ValidationError


def fail(code="INVALID_ZIP"):
    raise ValidationError(code)


def exact(file, size):
    data = file.read(size)
    if len(data) != size:
        fail("TRUNCATED_ZIP")
    return data


def extra_fields(data):
    fields = {}
    while data:
        if len(data) < 4:
            fail("INVALID_EXTRA_FIELD")
        key, size = struct.unpack_from("<HH", data)
        if size > len(data)-4 or key in fields:
            fail("INVALID_EXTRA_FIELD")
        fields[key] = data[4:4+size]
        data = data[4+size:]
    # Alternate Unicode names create competing Windows/path interpretations.
    if 0x7075 in fields:
        fail("UNSUPPORTED_UNICODE_PATH_EXTRA")
    return fields


def zip64_values(fields, expanded, compressed, offset=None, disk=None):
    values = [expanded, compressed] + ([] if offset is None else [offset, disk])
    raw = fields.get(1, b"")
    for i, value in enumerate(values):
        sentinel = 0xffff if i == 3 else 0xffffffff
        if value == sentinel:
            size = 4 if i == 3 else 8
            if len(raw) < size:
                fail("INVALID_ZIP64_EXTRA")
            values[i] = int.from_bytes(raw[:size], "little")
            raw = raw[size:]
    if raw:
        fail("AMBIGUOUS_ZIP64_EXTRA")
    return values


def safe_name(name):
    if not name or name.startswith("/") or "\\" in name or name.endswith("//"):
        fail("UNSAFE_MEMBER_PATH")
    folded = unicodedata.normalize("NFKC", name).casefold()
    for candidate in (name, folded):
        parts = candidate.rstrip("/").split("/")
        if any(not part or part in {".", ".."} or part.endswith((".", " "))
               or ":" in part or any(unicodedata.category(c).startswith("C") for c in part)
               or any(c in part for c in '<>"|?*')
               or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1,10)], *[f"LPT{i}" for i in range(1,10)]}
               for part in parts):
            fail("UNSAFE_MEMBER_PATH")
    return folded.rstrip("/")


@dataclass(frozen=True)
class Member:
    name: str
    key: str
    method: int
    crc: int
    compressed: int
    expanded: int
    local: int
    start: int
    end: int
    directory: bool


def inspect(file, limits: Limits):
    file.seek(0, 2)
    size = file.tell()
    if size > limits.archive_bytes:
        fail("ARCHIVE_SIZE_LIMIT")
    if size < 22:
        fail()
    file.seek(max(0, size-65557))
    tail = file.read(min(size, 65557))
    pos = tail.rfind(b"PK\x05\x06")
    if pos < 0 or len(tail)-pos < 22:
        fail("MISSING_EOCD")
    eocd = size-len(tail)+pos
    _, disk, cd_disk, disk_count, count, cd_size, cd_offset, comment = struct.unpack_from("<4s4H2LH", tail, pos)
    if eocd+22+comment != size or disk or cd_disk or disk_count != count:
        fail("SPLIT_OR_INVALID_EOCD")
    central_end = eocd
    file.seek(max(0, eocd-20))
    locator = file.read(20)
    has64 = locator[:4] == b"PK\x06\x07"
    if has64:
        _, ldisk, offset64, disks = struct.unpack("<4sLQL", locator)
        if ldisk or disks != 1 or offset64 > eocd-76:
            fail("INVALID_ZIP64_LOCATOR")
        file.seek(offset64)
        record = exact(file, 56)
        sig, length, made, needed, d1, d2, c1, c2, s64, o64 = struct.unpack("<4sQ2H2L4Q", record)
        if sig != b"PK\x06\x06" or not 44 <= length <= 65536 or offset64+12+length != eocd-20:
            fail("INVALID_ZIP64_RECORD")
        if d1 or d2 or c1 != c2 or needed > 45:
            fail("SPLIT_OR_UNSUPPORTED_ZIP64")
        if any(a != sentinel and a != b for a,b,sentinel in [
            (count,c2,0xffff), (cd_size,s64,0xffffffff), (cd_offset,o64,0xffffffff)]):
            fail("INCONSISTENT_ZIP64")
        count, cd_size, cd_offset, central_end = c2, s64, o64, offset64
    elif count == 0xffff or cd_size == 0xffffffff or cd_offset == 0xffffffff:
        fail("MISSING_ZIP64_RECORD")
    if not 0 < count <= limits.entries or cd_size > limits.directory_bytes:
        fail("DIRECTORY_RESOURCE_LIMIT")
    if cd_size < count*46 or cd_offset+cd_size != central_end:
        fail("INVALID_DIRECTORY_OFFSET")
    members, names, parents, expanded_total, compressed_total = [], {}, set(), 0, 0
    cursor = cd_offset
    for _ in range(count):
        if cursor+46 > central_end:
            fail("INVALID_DIRECTORY")
        file.seek(cursor)
        fields = struct.unpack("<4s6H3L5H2L", exact(file,46))
        sig, made, needed, flags, method, tm, dt, crc, compressed, expanded, nl, xl, cl, disk, ia, ea, offset = fields
        if sig != b"PK\x01\x02" or not 0 < nl <= limits.name_bytes or cursor+46+nl+xl+cl > central_end:
            fail("INVALID_DIRECTORY")
        if method not in (0,8):
            fail("UNSUPPORTED_COMPRESSION")
        if flags & (1 | 0x40):
            fail("ENCRYPTED_ARCHIVE")
        if flags & ~(0x080e if method == 8 else 0x0808) or needed > 45:
            fail("UNSUPPORTED_ZIP_FLAGS")
        raw_name = exact(file,nl)
        extras = extra_fields(exact(file,xl))
        try:
            name = raw_name.decode("utf-8" if flags & 0x800 else "cp437", errors="strict")
        except UnicodeError:
            fail("INVALID_MEMBER_ENCODING")
        expanded, compressed, offset, disk = zip64_values(extras, expanded, compressed, offset, disk)
        if disk:
            fail("SPLIT_ARCHIVE")
        key, directory = safe_name(name), name.endswith("/")
        mode = (ea >> 16) & 0xffff
        kind = stat.S_IFMT(mode)
        if kind not in (0,stat.S_IFREG,stat.S_IFDIR) or (kind == stat.S_IFDIR or ea & 0x10) and not directory:
            fail("LINK_OR_SPECIAL_ENTRY")
        if key in names or not directory and key in parents:
            fail("MEMBER_COLLISION")
        parts = key.split("/")
        for i in range(1,len(parts)):
            parent = "/".join(parts[:i])
            if parent in names and not names[parent]:
                fail("FILE_DIRECTORY_COLLISION")
            parents.add(parent)
        names[key] = directory
        if directory and expanded or expanded > limits.entry_bytes:
            fail("ENTRY_EXPANSION_LIMIT")
        if expanded > limits.ratio*compressed or (method == 0 and compressed != expanded):
            fail("COMPRESSION_RATIO_LIMIT")
        expanded_total += expanded
        compressed_total += compressed
        if expanded_total > limits.expanded_bytes:
            fail("TOTAL_EXPANSION_LIMIT")
        if offset+30 > cd_offset:
            fail("INVALID_LOCAL_OFFSET")
        file.seek(offset)
        lsig, ln, lf, lm, lt, ld, lc, cs, us, lnl, lxl = struct.unpack("<4s5H3L2H",exact(file,30))
        if lsig != b"PK\x03\x04" or lf != flags or lm != method or ln > 45:
            fail("LOCAL_HEADER_MISMATCH")
        if lnl != nl or offset+30+lnl+lxl > cd_offset or exact(file,lnl) != raw_name:
            fail("LOCAL_NAME_MISMATCH")
        local_extra = extra_fields(exact(file,lxl))
        lus, lcs = zip64_values(local_extra,us,cs)
        start = file.tell()
        end = start+compressed
        if end > cd_offset:
            fail("ENTRY_OUT_OF_BOUNDS")
        if flags & 8:
            if lc not in (0,crc) or lus not in (0,expanded) or lcs not in (0,compressed):
                fail("LOCAL_DESCRIPTOR_MISMATCH")
            file.seek(end)
            descriptor = file.read(min(24, cd_offset-end))
            wide = (us == 0xffffffff or cs == 0xffffffff or expanded > 0xffffffff or compressed > 0xffffffff)
            fmt, width = ("<LQQ",20) if wide else ("<LLL",12)
            found = False
            for prefix in ([4,0] if descriptor[:4] == b"PK\x07\x08" else [0]):
                if len(descriptor) >= prefix+width and struct.unpack_from(fmt,descriptor,prefix) == (crc,compressed,expanded):
                    end += prefix+width
                    found = True
                    break
            if not found:
                fail("INVALID_DATA_DESCRIPTOR")
        elif (lc,lcs,lus) != (crc,compressed,expanded):
            fail("LOCAL_SIZE_OR_CRC_MISMATCH")
        members.append(Member(name,key,method,crc,compressed,expanded,offset,start,end,directory))
        cursor += 46+nl+xl+cl
    if cursor != central_end or expanded_total > limits.ratio*compressed_total:
        fail("INVALID_DIRECTORY_OR_RATIO")
    previous = 0
    for local, end in sorted((m.local,m.end) for m in members):
        if local < previous:
            fail("OVERLAPPING_ENTRIES")
        if local != previous:
            fail("UNREFERENCED_ZIP_REGION")
        previous = end
    if previous != cd_offset:
        fail("UNREFERENCED_ZIP_REGION")
    return members, size


def chunks(file, member: Member, limits: Limits, counters: list[int]):
    """Bounded deflate output; never trust ZipExtFile's declared-size truncation."""
    file.seek(member.start)
    remaining, produced, crc = member.compressed, 0, 0
    decoder = zlib.decompressobj(-15) if member.method == 8 else None
    while remaining:
        data = exact(file,min(remaining,limits.block_bytes))
        remaining -= len(data)
        while data:
            if decoder:
                try:
                    output = decoder.decompress(data, limits.block_bytes)
                except zlib.error:
                    fail("DEFLATE_ERROR")
                data = decoder.unconsumed_tail
                if decoder.unused_data:
                    fail("TRAILING_COMPRESSED_DATA")
            else:
                output, data = data, b""
            produced += len(output)
            counters[0] += len(output)
            if produced > min(member.expanded,limits.entry_bytes) or counters[0] > limits.expanded_bytes:
                fail("ACTUAL_EXPANSION_LIMIT")
            if produced > limits.ratio*member.compressed:
                fail("ACTUAL_RATIO_LIMIT")
            crc = zlib.crc32(output,crc)
            if output:
                yield output
    if decoder and not decoder.eof:
        fail("TRUNCATED_DEFLATE")
    if produced != member.expanded or crc != member.crc:
        fail("CRC_OR_SIZE_FAILURE")
