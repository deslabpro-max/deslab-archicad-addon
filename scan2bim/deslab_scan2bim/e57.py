"""Minimal ASTM E57 point reader on numpy only (no libE57Format).

Reads the cartesian (or spherical) coordinates of every scan in data3D, applies each scan's
pose and returns one float32 (N, 3) array in metres. Supports the bitpack codec, which is the
only codec the standard defines; fields of any bit width are decoded, byte-aligned ones fast.
"""

from __future__ import annotations

import math
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np

PAGE = 1024
PAGE_DATA = 1020
NS = "{http://www.astm.org/COMMIT/E57/2010-e57-v1.0}"


@dataclass
class Field:
    name: str
    kind: str  # Float, ScaledInteger, Integer
    bits: int
    minimum: int = 0
    scale: float = 1.0
    offset: float = 0.0


class E57File:
    def __init__(self, path: str):
        self.f = open(path, "rb")
        head = self.f.read(48)
        sig, _maj, _min, self.length, xml_off, xml_len, page = struct.unpack("<8sIIQQQQ", head)
        if sig != b"ASTM-E57":
            raise ValueError("not an E57 file")
        if page != PAGE:
            raise ValueError(f"unsupported page size {page}")
        self.root = ET.fromstring(self.read_logical(xml_off, xml_len))

    def close(self):
        self.f.close()

    # E57 stores a CRC in the last 4 bytes of every 1024-byte page; "logical" data skips them.
    def read_logical(self, phys_off: int, n: int) -> bytes:
        out = bytearray()
        off = phys_off
        while len(out) < n:
            in_page = off % PAGE
            take = min(PAGE_DATA - in_page, n - len(out))
            self.f.seek(off)
            out += self.f.read(take)
            off = (off // PAGE + 1) * PAGE
        return bytes(out)

    @staticmethod
    def logical_to_physical(phys_start: int, logical_delta: int) -> int:
        # phys_start may sit mid-page; walk forward logical_delta data bytes.
        in_page = phys_start % PAGE
        total = in_page + logical_delta
        return (phys_start - in_page) + (total // PAGE_DATA) * PAGE + total % PAGE_DATA

    def scans(self):
        data3d = self.root.find(NS + "data3D")
        return [] if data3d is None else list(data3d)


def _num(node, default=0.0):
    if node is None or node.text is None or not node.text.strip():
        return default
    return float(node.text)


def _fields(prototype) -> list[Field]:
    fields = []
    for child in prototype:
        name = child.tag.replace(NS, "")
        kind = child.get("type")
        if kind == "Float":
            bits = 32 if child.get("precision") == "single" else 64
            fields.append(Field(name, kind, bits))
        elif kind in ("Integer", "ScaledInteger"):
            lo = int(child.get("minimum", "-9223372036854775808"))
            hi = int(child.get("maximum", "9223372036854775807"))
            bits = max(0, math.ceil(math.log2(hi - lo + 1))) if hi > lo else 0
            fields.append(Field(name, kind, bits, lo,
                                float(child.get("scale", "1")), float(child.get("offset", "0"))))
        else:
            raise ValueError(f"unsupported E57 field type {kind} ({name})")
    return fields


def _decode(buf: bytes, fld: Field, count: int) -> np.ndarray:
    if fld.kind == "Float":
        arr = np.frombuffer(buf, dtype="<f4" if fld.bits == 32 else "<f8", count=count)
        return arr.astype(np.float64)
    if fld.bits == 0:
        raw = np.zeros(count, dtype=np.int64)
    elif fld.bits in (8, 16, 32, 64):
        raw = np.frombuffer(buf, dtype={8: "<u1", 16: "<u2", 32: "<u4", 64: "<u8"}[fld.bits],
                            count=count).astype(np.int64)
    else:  # generic LSB-first bit unpacking
        bits = np.unpackbits(np.frombuffer(buf, dtype=np.uint8), bitorder="little")
        bits = bits[: count * fld.bits].reshape(count, fld.bits).astype(np.int64)
        raw = (bits << np.arange(fld.bits, dtype=np.int64)).sum(axis=1)
    val = raw + fld.minimum
    if fld.kind == "ScaledInteger":
        return val * fld.scale + fld.offset
    return val.astype(np.float64)


def _read_compressed_vector(e57: E57File, points_node, fields: list[Field], wanted: set[str],
                            step: int) -> dict[str, np.ndarray]:
    section = int(points_node.get("fileOffset"))
    count = int(points_node.get("recordCount"))
    hdr = e57.read_logical(section, 32)
    sec_id, _r, sec_len, data_off, _index_off = struct.unpack("<B7sQQQ", hdr)
    if sec_id != 1:
        raise ValueError("bad compressed vector section")
    streams = [bytearray() for _ in fields]
    pos = data_off
    end = e57.logical_to_physical(section, sec_len)
    while pos < end:
        head = e57.read_logical(pos, 4)
        ptype, _flags, len_m1 = struct.unpack("<BBH", head)
        plen = len_m1 + 1
        if ptype == 1:
            packet = e57.read_logical(pos, plen)
            nstreams = struct.unpack_from("<H", packet, 4)[0]
            lens = struct.unpack_from("<" + "H" * nstreams, packet, 6)
            p = 6 + 2 * nstreams
            for i, ln in enumerate(lens):
                if fields[i].name in wanted:
                    streams[i] += packet[p:p + ln]
                p += ln
        elif ptype not in (0, 2):
            raise ValueError(f"bad E57 packet type {ptype}")
        pos = e57.logical_to_physical(pos, plen)
    out = {}
    for fld, buf in zip(fields, streams):
        if fld.name in wanted:
            out[fld.name] = _decode(bytes(buf), fld, count)[::step]
    return out


def _quat_matrix(w, x, y, z):
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def read_points(path: str, step: int = 1) -> np.ndarray:
    """All points of all scans as float32 (N, 3), world coordinates. step>1 keeps every n-th."""
    e57 = E57File(path)
    try:
        chunks = []
        for scan in e57.scans():
            pts_node = scan.find(NS + "points")
            proto = pts_node.find(NS + "prototype")
            fields = _fields(proto)
            names = {f.name for f in fields}
            cart = {"cartesianX", "cartesianY", "cartesianZ"}
            sph = {"sphericalRange", "sphericalAzimuth", "sphericalElevation"}
            wanted = cart if cart <= names else sph
            if not wanted <= names:
                raise ValueError("scan has neither cartesian nor spherical coordinates")
            if "cartesianInvalidState" in names:
                wanted = wanted | {"cartesianInvalidState"}
            if "sphericalInvalidState" in names:
                wanted = wanted | {"sphericalInvalidState"}
            cols = _read_compressed_vector(e57, pts_node, fields, wanted, step)
            if "cartesianX" in wanted:
                xyz = np.stack([cols["cartesianX"], cols["cartesianY"], cols["cartesianZ"]], 1)
                bad = cols.get("cartesianInvalidState")
            else:
                r, az, el = cols["sphericalRange"], cols["sphericalAzimuth"], cols["sphericalElevation"]
                xyz = np.stack([r * np.cos(el) * np.cos(az), r * np.cos(el) * np.sin(az), r * np.sin(el)], 1)
                bad = cols.get("sphericalInvalidState")
            if bad is not None:
                xyz = xyz[bad == 0]
            pose = scan.find(NS + "pose")
            if pose is not None:
                rot = pose.find(NS + "rotation")
                tr = pose.find(NS + "translation")
                if rot is not None:
                    m = _quat_matrix(_num(rot.find(NS + "w"), 1.0), _num(rot.find(NS + "x")),
                                     _num(rot.find(NS + "y")), _num(rot.find(NS + "z")))
                    xyz = xyz @ m.T
                if tr is not None:
                    xyz = xyz + np.array([_num(tr.find(NS + "x")), _num(tr.find(NS + "y")),
                                          _num(tr.find(NS + "z"))])
            chunks.append(xyz.astype(np.float32))
        if not chunks:
            raise ValueError("E57 file has no scans")
        return np.concatenate(chunks)
    finally:
        e57.close()
