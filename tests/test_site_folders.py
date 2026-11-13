"""Completed albums go to Downloads/E-Hentai or Downloads/nhentai."""

from pathlib import Path

from src.config import DOWNLOAD_FOLDER, EHENTAI_SUBFOLDER, NHENTAI_SUBFOLDER
from src.downloader.album_downloader import AlbumDownloader
from src.downloader.nhentai_downloader import NHentaiAlbumDownloader


def _bare(cls, title):
    """A downloader object without network access (no __init__)."""
    obj = cls.__new__(cls)
    obj.clean_album_name = obj.album_name = title
    return obj


def test_each_site_has_its_folder():
    assert _bare(AlbumDownloader, "A").site_download_folder == Path(DOWNLOAD_FOLDER) / EHENTAI_SUBFOLDER
    assert _bare(NHentaiAlbumDownloader, "A").site_download_folder == Path(DOWNLOAD_FOLDER) / NHENTAI_SUBFOLDER


def test_cbz_goes_in_the_site_folder():
    cbz = _bare(NHentaiAlbumDownloader, "My: title")._cbz_path()
    assert cbz == Path(DOWNLOAD_FOLDER) / NHENTAI_SUBFOLDER / "My_ title.cbz"


def test_long_cjk_cbz_name_fits_255_bytes():
    cbz = _bare(AlbumDownloader, "原神大放送" * 40)._cbz_path()
    assert len(cbz.name.encode("utf-8")) <= 255


def test_archive_of_an_older_version_in_downloads_is_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old = tmp_path / DOWNLOAD_FOLDER / "Old album.cbz"
    old.parent.mkdir()
    import zipfile
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("01.jpg", b"x")
        zf.writestr("02.jpg", b"x")
    album = _bare(AlbumDownloader, "Old album")
    album.total_images = 2
    assert album._find_complete_cbz() == Path(DOWNLOAD_FOLDER) / "Old album.cbz"
