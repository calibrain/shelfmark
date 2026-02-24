"""Tests for Torznab XML parsing helpers."""

from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml


def test_parse_torznab_xml_parses_basic_item():
    xml_text = """<?xml version="1.0"?>
<rss>
  <channel>
    <item>
      <title>Example Release</title>
      <guid>abc-123</guid>
      <link>https://example.com/download</link>
      <size>12345</size>
      <enclosure type="application/x-bittorrent" url="https://example.com/file.torrent" />
      <prowlarrindexer id="42">Test Indexer</prowlarrindexer>
      <newznab:attr xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"
                    name="seeders" value="10" />
      <newznab:attr xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"
                    name="peers" value="15" />
    </item>
  </channel>
</rss>
"""
    results = parse_torznab_xml(xml_text)

    assert len(results) == 1
    result = results[0]
    assert result["title"] == "Example Release"
    assert result["guid"] == "abc-123"
    assert result["indexerId"] == 42
    assert result["indexer"] == "Test Indexer"
    assert result["protocol"] == "torrent"
    assert result["seeders"] == 10
    assert result["leechers"] == 5


def test_parse_torznab_xml_rejects_entity_expansion_payload():
    xml_text = """<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<rss>
  <channel>
    <item>
      <title>&xxe;</title>
    </item>
  </channel>
</rss>
"""
    assert parse_torznab_xml(xml_text) == []
