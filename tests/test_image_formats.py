"""Labels of the "Image Format" field and the notices shown when a format is chosen."""

from src import image_formats as F


def test_field_order_and_default():
    assert F.LABELS == ("Native (Original)", "WEBP (.webp)", "JPEG (.jpg)", "JPEG XL (.jxl)",
                        "PNG (.png)", "AVIF (.avif)")
    assert F.DEFAULT_LABEL == "WEBP (.webp)"
    assert F.normalize_label(None) == "WEBP (.webp)"


def test_labels_map_to_names():
    assert [F.name_from_label(label) for label in F.LABELS] == ["original", "webp", "jpg", "jxl", "png", "avif"]


def test_old_saved_labels():
    assert F.normalize_label("JPG (.jpg)") == "JPEG (.jpg)"
    assert F.normalize_label("AUTO (JPG / Native if animated)") == "WEBP (.webp)"


def test_names_and_aliases():
    assert F.normalize_name("JPEG") == "jpg"
    assert F.normalize_name(".AVIF") == "avif"
    assert F.normalize_name("native") == "original"
    assert F.normalize_name("auto") == "webp"


def test_notices():
    assert F.notice("original") == "" and F.notice("webp") == ""
    for name in ("jpg", "jxl", "png"):
        assert "first frame" in F.notice(name)
    for name in ("jxl", "avif"):
        assert "Microsoft Store" in F.notice(name)
    assert "first frame" not in F.notice("avif")


def test_windows_extensions_and_log_line():
    name, page, app = F.windows_extension("avif")
    assert name == "AV1 Video Extension"
    assert page == "https://apps.microsoft.com/detail/9mvzqvxjbq9v"
    assert app == "ms-windows-store://pdp/?ProductId=9MVZQVXJBQ9V&mode=mini"
    assert F.product_id_from_url(page) == "9MVZQVXJBQ9V"
    assert F.product_id_from_url("https://example.org/x") == ""
    assert F.windows_extension("webp") is None
    assert F.log_line("webp") == ""
    assert "first frame" in F.log_line("jxl") and "9mzprth5c0tb" in F.log_line("jxl")


def test_notice_snooze():
    settings = {}
    assert not F.notice_hidden(settings, "avif")
    F.hide_notice(settings, "avif")
    assert F.notice_hidden(settings, "avif") and not F.notice_hidden(settings, "jpg")
    settings["format_notice_hidden_until"] = {"avif": 1}  # expired
    assert not F.notice_hidden(settings, "avif")
    assert not F.notice_hidden({"format_notice_hidden_until": "broken"}, "avif")
