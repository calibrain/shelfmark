from shelfmark.core.path_mappings import (
    RemotePathMapping,
    remap_remote_to_local_with_match,
)


def test_remap_rejects_parent_directory_remainder(tmp_path):
    mapping = RemotePathMapping(
        host="qbittorrent",
        remote_path="/remote/downloads",
        local_path=str(tmp_path / "local" / "downloads"),
    )
    remote_path = "/remote/downloads/../outside/book.epub"

    remapped, matched = remap_remote_to_local_with_match(
        mappings=[mapping],
        host="qbittorrent",
        remote_path=remote_path,
    )

    assert matched is True
    assert remapped is None


def test_remap_rejects_path_that_resolves_outside_local_prefix(tmp_path):
    local_prefix = tmp_path / "local" / "downloads"
    mapping = RemotePathMapping(
        host="qbittorrent",
        remote_path="/remote/downloads",
        local_path=str(local_prefix),
    )
    remote_path = "/remote/downloads/subdir/../../outside/book.epub"

    remapped, matched = remap_remote_to_local_with_match(
        mappings=[mapping],
        host="qbittorrent",
        remote_path=remote_path,
    )

    assert matched is True
    assert remapped is None


def test_remap_allows_normal_child_path_under_local_prefix(tmp_path):
    local_prefix = tmp_path / "local" / "downloads"
    mapping = RemotePathMapping(
        host="qbittorrent",
        remote_path="/remote/downloads",
        local_path=str(local_prefix),
    )

    remapped, matched = remap_remote_to_local_with_match(
        mappings=[mapping],
        host="qbittorrent",
        remote_path="/remote/downloads/author/book.epub",
    )

    assert matched is True
    assert remapped == local_prefix / "author" / "book.epub"
