import base64
import io

from PIL import Image, ImageDraw

from ptouch_connector import layout, protocol, render


def png_data_url(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def sample_image():
    img = Image.new("RGBA", (300, 150), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([20, 20, 280, 130], fill=(200, 30, 30, 255))
    return png_data_url(img)


def full_spec(**kw):
    spec = dict(layout.DEFAULT_SPEC)
    spec["items"] = [
        {"type": "image", "src": sample_image(), "mode": "dither"},
        {"type": "text", "text": "Kabel {n}\\nRouter", "bold": True, "italic": True},
        {"type": "qr", "data": "https://example.org/{n}"},
        {"type": "barcode", "kind": "code128", "data": "ABC-{n}"},
        {"type": "spacer", "width_mm": 3},
    ]
    spec.update(kw)
    return spec


def test_all_items_all_tapes():
    for tape in protocol.TAPE_SPECS:
        img, warnings = layout.render_label(full_spec(frame="round"), tape, "7")
        assert img.height == protocol.tape_spec(tape)[1]
        hist = img.histogram()
        assert sum(hist[1:255]) == 0  # nur Schwarz und Weiß
        lines = render.to_raster_lines(img, tape)
        assert len(lines) == img.width


def test_fixed_length_and_invert():
    img, w = layout.render_label(full_spec(length_mm=200, invert=True), 24)
    assert abs(img.width - int(200 * layout.MM)) <= 1
    assert img.getpixel((1, 1)) == 0  # invertierter Hintergrund
    img, w = layout.render_label(full_spec(length_mm=5), 24)
    assert any("abgeschnitten" in m for m in w)


def test_series():
    spec = full_spec(series={"mode": "numbers", "start": 1, "end": 3, "step": 1, "digits": 2})
    imgs, warnings, values = layout.render_all(spec, 12)
    assert values == ["01", "02", "03"] and len(imgs) == 3
    spec = full_spec(series={"mode": "list", "list": "A\n\nB\n"})
    assert layout.render_all(spec, 12)[2] == ["A", "B"]


def test_bad_barcode_gives_warning():
    spec = {"items": [{"type": "barcode", "kind": "ean13", "data": "abc"}]}
    img, w = layout.render_label(spec, 12)
    assert w and "Barcode" in w[0]


def test_multi_job():
    pages = [[b"\xff" * 16] * 3, [bytes(16)] * 2]
    job = protocol.build_multi_job(pages, 12)
    assert job.count(b"\x1b\x69\x7a") == 2
    assert job.endswith(b"\x1a") and b"\x0c\x1b\x69\x7a" in job


def test_broken_font_falls_back(monkeypatch):
    calls = {"n": 0}
    real = layout._font

    def flaky(name, size):
        if name == "Kaputt":
            calls["n"] += 1
            raise OSError("division by zero")
        return real(name, size)

    monkeypatch.setattr(layout, "_font", flaky)
    monkeypatch.setattr(layout, "list_fonts", lambda: {"Kaputt": "/x.ttf"})
    img, w = layout.render_label({"items": [{"type": "text", "text": "Hallo", "font": "Kaputt", "bold": True}]}, 12)
    assert calls["n"] >= 1 and img.width > 20
    assert any("Kaputt" in m and "Ersatzschrift" in m for m in w)


def test_failing_item_does_not_break_label(monkeypatch):
    def boom(*a, **k):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(layout, "render_qr", boom)
    img, w = layout.render_label({"items": [{"type": "text", "text": "OK"}, {"type": "qr", "data": "x"}]}, 12)
    assert img.width > 10 and any("QR-Code konnte nicht" in m for m in w)


def test_broken_default_font_uses_builtin(monkeypatch):
    real = layout._font

    def flaky(name, size):
        if name != "__builtin__":
            raise OSError("division by zero")
        return real(name, size)

    monkeypatch.setattr(layout, "_font", flaky)
    img, w = layout.render_label({"items": [{"type": "text", "text": "Hallo"}]}, 12)
    assert img.width > 20 and any("Ersatzschrift" in m for m in w)
