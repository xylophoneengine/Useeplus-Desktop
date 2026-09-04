from pathlib import Path
import cv2
import numpy as np
from microscope.useeplus import (Packet, parse_packet, FrameAssembler,
                                 iter_packet_file, BUTTON_FLAG)

DATA = Path(__file__).parent / "data" / "packets_640x480.bin"


def make_pkt(cid=7, fid=1, cam=0, flags=0, gsensor=0, chunk=b"\xff\xd8abc"):
    body = bytes([fid, cam, flags]) + gsensor.to_bytes(4, "little", signed=True) + chunk
    return b"\xaa\xbb" + bytes([cid]) + len(body).to_bytes(2, "little") + body


def test_parse_packet_fields():
    p = parse_packet(make_pkt(cid=11, fid=5, cam=1, flags=BUTTON_FLAG, gsensor=-3, chunk=b"xyz"))
    assert p == Packet(cid=11, fid=5, cam=1, flags=BUTTON_FLAG, gsensor=-3, chunk=b"xyz")


def test_parse_packet_rejects_bad_magic_cid_short():
    assert parse_packet(b"\xbb\xaa" + make_pkt()[2:]) is None
    assert parse_packet(make_pkt(cid=3)) is None
    assert parse_packet(b"\xaa\xbb\x07\x00") is None


def test_parse_packet_truncates_chunk_to_declared_length():
    pkt = make_pkt(chunk=b"12345") + b"PADDING"
    assert parse_packet(pkt).chunk == b"12345"


def test_assembler_emits_on_fid_change_and_validates_jpeg():
    a = FrameAssembler()
    assert a.feed(parse_packet(make_pkt(fid=1, chunk=b"\xff\xd8AA"))) is None
    assert a.feed(parse_packet(make_pkt(fid=1, chunk=b"BB\xff\xd9"))) is None
    out = a.feed(parse_packet(make_pkt(fid=2, chunk=b"\xff\xd8CC", flags=BUTTON_FLAG)))
    assert out == b"\xff\xd8AABB\xff\xd9"
    assert a.last_flags == BUTTON_FLAG


def test_assembler_drops_frame_without_soi_eoi():
    a = FrameAssembler()
    a.feed(parse_packet(make_pkt(fid=1, chunk=b"garbage")))
    assert a.feed(parse_packet(make_pkt(fid=2, chunk=b"\xff\xd8"))) is None


def test_recorded_packets_yield_decodable_frames():
    a = FrameAssembler()
    frames = []
    for raw in iter_packet_file(DATA):
        p = parse_packet(raw)
        if p is None:
            continue
        jpeg = a.feed(p)
        if jpeg:
            frames.append(jpeg)
    assert len(frames) >= 10
    for j in frames:
        img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
        assert img is not None and img.shape == (480, 640, 3)
