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
    # Gesamtlänge = Rand + Inhalt + Rand
    assert abs(layout.total_length_mm(full_spec(), img.width) - 200) < 0.3
    assert img.getpixel((1, 1)) == 0  # invertierter Hintergrund
    img, w = layout.render_label(full_spec(length_mm=5), 24)
    assert any("abgeschnitten" in m for m in w)


def test_series():
    spec = full_spec(series={"mode": "numbers", "start": 1, "end": 3, "step": 1, "digits": 2})
    imgs, warnings, values = layout.render_all(spec, 12)[:3]
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


def test_number_left_with_divider_fixed_length():
    spec = {"length_mm": 90, "align": "center", "gap_mm": 2,
            "items": [{"type": "number", "position": "left", "divider": True, "divider_px": 2, "bold": True},
                      {"type": "text", "text": "AB"}],
            "series": {"mode": "numbers", "rows": [{"value": "04"}, {"value": "05", "items": [
                {"type": "number", "position": "right"}, {"type": "text", "text": "C"}]}]}}
    imgs, warnings, values = layout.render_all(spec, 24)[:3]
    assert values == ["04", "05"] and not warnings
    img = imgs[0]
    assert abs(layout.total_length_mm(spec, img.width) - 90) < 0.3
    # Trennlinie: eine Spalte, die über die ganze Höhe schwarz ist
    cols = [x for x in range(img.width) if all(img.getpixel((x, y)) == 0 for y in range(img.height))]
    assert cols and cols[0] < img.width // 2
    # Nummer rechts beim zweiten Etikett: Tinte am rechten Rand, keine Trennlinie
    img2 = imgs[1]
    right_ink = any(img2.getpixel((x, y)) == 0 for x in range(img2.width - 60, img2.width) for y in range(img2.height))
    assert right_ink


def test_legacy_series_still_works():
    spec = {"items": [{"type": "text", "text": "{n}"}], "series": {"mode": "list", "list": "a\nb"}}
    assert layout.render_all(spec, 12)[2] == ["a", "b"]


def test_length_offset_correction():
    spec = {"length_mm": 60, "margin_mm": 2, "_length_offset_mm": 3, "items": [{"type": "text", "text": "A"}]}
    img, w = layout.render_label(spec, 12)
    assert not w
    assert abs(layout.total_length_mm(spec, img.width) - 60) < 0.3
    plain = dict(spec, _length_offset_mm=0)
    assert layout.render_label(plain, 12)[0].width - img.width == round(3 * layout.MM)


def test_too_short_length_warns():
    img, w = layout.render_label({"length_mm": 3, "items": [{"type": "text", "text": "A"}]}, 12)
    assert any("zu kurz" in m for m in w)


def test_qr_right_position_with_fixed_length():
    spec = {"length_mm": 80, "items": [{"type": "text", "text": "AB"}, {"type": "qr", "data": "1", "position": "right"}]}
    img, w = layout.render_label(spec, 24)
    ink_right = [x for x in range(img.width) if any(img.getpixel((x, y)) == 0 for y in range(img.height))]
    assert ink_right[-1] > img.width - 20


def test_autofit_keeps_fixed_length():
    spec = {"length_mm": 40, "items": [{"type": "number", "position": "left", "bold": True},
                                        {"type": "qr", "data": "{n}", "position": "right"}]}
    img, w = layout.render_label(spec, 24, "00878")
    assert abs(layout.total_length_mm(spec, img.width) - 40) < 0.3
    assert any("verkleinert" in m for m in w) and not any("abgeschnitten" in m for m in w)


def _transponder_spec(uniform=True):
    return {"length_mm": 37, "margin_mm": 2, "padding_mm": 0.5,
            "items": [{"type": "text", "text": "{name}\n{nr}\n{raum}", "bold": True, "align": "center", "line_spacing": 6}],
            "series": {"mode": "on", "uniform": uniform, "rows": [
                {"value": "2397", "fields": {"name": "Editing Room", "nr": "2", "raum": "C1.22"}},
                {"value": "3351", "fields": {"name": "Band Room", "nr": "3", "raum": "B-1.03 · Night 24/5"}, "on": False},
                {"value": "3997", "fields": {"name": "Piano", "nr": "Room", "raum": "A0.01"}}]}}


def test_table_columns_and_selection():
    imgs, w, values, idx = layout.render_all(_transponder_spec(), 24, only_selected=True)
    assert values == ["2397", "3997"] and idx == [0, 2]
    for img in imgs:
        assert abs(layout.total_length_mm(_transponder_spec(), img.width) - 37) < 0.3


def test_uniform_font_size_same_ink_height():
    def ink_rows(img):
        return [y for y in range(img.height) if any(img.getpixel((x, y)) == 0 for x in range(img.width))]
    imgs = layout.render_all(_transponder_spec(True), 24)[0]
    # gleiche Schriftgröße -> „Editing Room“ und „Piano“ (beide ohne Unterlängen) gleich hoch
    heights = [len(ink_rows(i)) for i in imgs]
    assert max(heights) - min(heights) <= 6, heights
    loose = layout.render_all(_transponder_spec(False), 24)[0]
    assert len(ink_rows(loose[2])) >= len(ink_rows(imgs[2]))


def test_uniform_is_thread_safe():
    import concurrent.futures as cf
    spec = _transponder_spec(True)
    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda _: layout.render_all(spec, 24)[0][0].width, range(24)))
    assert len(set(results)) == 1
