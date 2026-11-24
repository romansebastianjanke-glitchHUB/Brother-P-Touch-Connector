import pytest
from PIL import Image

from ptouch_connector import protocol, render


def unpackbits(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        n = data[i]
        i += 1
        if n < 128:
            out += data[i : i + n + 1]
            i += n + 1
        elif n > 128:
            out += bytes([data[i]]) * (257 - n)
            i += 1
    return bytes(out)


@pytest.mark.parametrize("data", [
    b"\x00" * 16,
    b"\xff" * 16,
    bytes(range(16)),
    b"\x00\x00\x01\x02\x02\x02\x03\x00" * 2,
    b"\xaa",
    bytes(300),
])
def test_packbits_roundtrip(data):
    assert unpackbits(protocol.packbits(data)) == data


def test_empty_line_is_zero_command():
    assert protocol.raster_line(bytes(16)) == b"Z"


def test_raster_line_g_command():
    line = b"\xff" * 16
    cmd = protocol.raster_line(line)
    assert cmd[0:1] == b"G"
    n = int.from_bytes(cmd[1:3], "little")
    assert unpackbits(cmd[3 : 3 + n]) == line


def test_print_info_layout():
    cmd = protocol.print_info(12, 300)
    assert cmd[:3] == b"\x1b\x69\x7a"
    assert cmd[3] == 0x86 and cmd[4] == 0x01 and cmd[5] == 12
    assert int.from_bytes(cmd[7:11], "little") == 300
    assert len(cmd) == 13


def test_job_structure():
    job = protocol.build_job([bytes(16), b"\xff" * 16], 12)
    assert job.startswith(b"\x00" * 100 + b"\x1b\x40" + b"\x1b\x69\x61\x01")
    assert b"\x4d\x02" in job
    assert job.endswith(b"\x1a")


def test_parse_status():
    raw = bytearray(32)
    raw[0], raw[1], raw[2], raw[3] = 0x80, 0x20, 0x42, 0x30
    raw[10], raw[11] = 12, 0x01
    st = protocol.parse_status(bytes(raw))
    assert st.ok and st.media_width_mm == 12
    raw[9] = 0x10
    st = protocol.parse_status(bytes(raw))
    assert not st.ok and "Deckel offen" in st.errors


def test_parse_status_rejects_garbage():
    with pytest.raises(ValueError):
        protocol.parse_status(b"\x00" * 10)


@pytest.mark.parametrize("width", list(protocol.TAPE_SPECS))
def test_pixels_stay_in_print_area(width):
    left, pins = protocol.tape_spec(width)
    img = Image.new("1", (3, pins), 0)  # alles schwarz
    for line in render.to_raster_lines(img, width):
        bits = "".join(f"{b:08b}" for b in line)
        # Pin p liegt an Bitposition 127 - p
        set_pins = {127 - i for i, c in enumerate(bits) if c == "1"}
        assert set_pins == set(range(left, left + pins))


def test_render_text_height_matches_tape():
    for width in protocol.TAPE_SPECS:
        img = render.render_text("Hallo", width)
        assert img.height == protocol.tape_spec(width)[1]
        assert img.width > 10


def test_orientation_top_row_goes_to_highest_pin():
    # Am PT-P710BT bestätigt: ohne diese Zuordnung druckt er seitenverkehrt
    left, pins = protocol.tape_spec(12)
    img = Image.new("1", (1, pins), 1)
    img.putpixel((0, 0), 0)  # oberste Bildzeile
    line = render.to_raster_lines(img, 12)[0]
    bits = "".join(f"{b:08b}" for b in line)
    set_pins = {127 - i for i, c in enumerate(bits) if c == "1"}
    assert set_pins == {left + pins - 1}
    mirrored = render.to_raster_lines(img, 12, mirror=True)[0]
    bits = "".join(f"{b:08b}" for b in mirrored)
    assert {127 - i for i, c in enumerate(bits) if c == "1"} == {left}
