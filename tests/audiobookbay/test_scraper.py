"""
Tests for AudiobookBay scraper functions.
"""

from unittest.mock import Mock, patch
import pytest
import requests

from shelfmark.release_sources.audiobookbay import scraper


# Mock HTML based on real ABB structure
SAMPLE_SEARCH_HTML = """
<html>
<body>
<div class="post">
    <div class="postTitle"><h2><a href="/abss/test-book-title-by-author/" rel="bookmark">Test Book Title - Test Author</a></h2></div>
    <div class="postInfo">Category: Genre&nbsp; <br>Language: English<span style="margin-left:100px;">Keywords: Test Keywords&nbsp;</span><br></div>
    <div class="postContent">
        <div class="center">
            <p class="center">Shared by:<a href="/member/users/index?&mode=userinfo&username=testuser">testuser</a></p>
            <p class="center"><a href="https://audiobookbay.lu/abss/test-book-title-by-author/"><img src="https://example.com/cover.jpg" alt="Test Cover" width="250"></a></p>
        </div>
        <p style="text-align:center;">Posted: 01 Jan 2024<br>Format: <span style="color:#a00;">M4B</span> / Bitrate: <span style="color:#a00;">128 Kbps</span><br>File Size: <span style="color:#00f;">500.00</span> MBs</p>
    </div>
    <div class="postMeta">
        <span class="postLink"><a href="https://audiobookbay.lu/abss/test-book-title-by-author/">Audiobook Details</a></span>
        <span class="postComments"><a href="/dload-now?ll=test" rel="nofollow">Direct Download</a></span>
    </div>
</div>
<div class="post">
    <div class="postTitle"><h2><a href="/abss/another-test-book/" rel="bookmark">Another Test Book - Another Author</a></h2></div>
    <div class="postInfo">Category: Fiction&nbsp; <br>Language: Spanish<span style="margin-left:100px;">Keywords: Test&nbsp;</span><br></div>
    <div class="postContent">
        <div class="center">
            <p class="center">Shared by:<a href="/member/users/index?&mode=userinfo&username=user2">user2</a></p>
            <p class="center"><a href="https://audiobookbay.lu/abss/another-test-book/"><img src="https://example.com/cover2.jpg" alt="Cover 2" width="250"></a></p>
        </div>
        <p style="text-align:center;">Posted: 15 Nov 2023<br>Format: <span style="color:#a00;">MP3</span> / Bitrate: <span style="color:#a00;">256 Kbps</span><br>File Size: <span style="color:#00f;">1.01</span> GBs</p>
    </div>
    <div class="postMeta">
        <span class="postLink"><a href="https://audiobookbay.lu/abss/another-test-book/">Audiobook Details</a></span>
        <span class="postComments"><a href="/dload-now?ll=test2" rel="nofollow">Direct Download</a></span>
    </div>
</div>
</body>
</html>
"""

EMPTY_SEARCH_HTML = """
<html>
<body>
</body>
</html>
"""

# Mock HTML for detail page with info hash and trackers
SAMPLE_DETAIL_HTML = """
<html>
<body>
<table>
    <tr>
        <td>Info Hash</td>
        <td>ABC123DEF456GHI789JKL012MNO345PQR678STU</td>
    </tr>
    <tr>
        <td>Tracker 1</td>
        <td>udp://tracker.openbittorrent.com:80</td>
    </tr>
    <tr>
        <td>Tracker 2</td>
        <td>http://tracker.example.com:8080</td>
    </tr>
    <tr>
        <td>Other Info</td>
        <td>Some other data</td>
    </tr>
</table>
</body>
</html>
"""

DETAIL_HTML_NO_TRACKERS = """
<html>
<body>
<table>
    <tr>
        <td>Info Hash</td>
        <td>ABC123DEF456GHI789JKL012MNO345PQR678STU</td>
    </tr>
</table>
</body>
</html>
"""


class TestSearchAudiobookbay:
    """Tests for the search_audiobookbay function."""

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    @patch('shelfmark.release_sources.audiobookbay.scraper.config.get')
    def test_search_audiobookbay_success(self, mock_config_get, mock_get_proxies, mock_get):
        """Test successful search with results."""
        mock_config_get.return_value = 1.0  # rate_limit_delay
        mock_get_proxies.return_value = {}
        
        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://audiobookbay.lu/page/1/?s=test+query&cat=undefined%2Cundefined"
        mock_response.text = SAMPLE_SEARCH_HTML
        mock_get.return_value = mock_response
        
        results = scraper.search_audiobookbay("test query", max_pages=1, hostname="audiobookbay.lu")
        
        assert len(results) == 2
        assert results[0]['title'] == "Test Book Title - Test Author"
        assert results[0]['link'] == "https://audiobookbay.lu/abss/test-book-title-by-author/"
        assert results[0]['language'] == "English"
        assert results[0]['format'] == "M4B"
        assert results[0]['bitrate'] == "128 Kbps"
        assert results[0]['size'] == "500.00 MBs"
        assert results[0]['posted_date'] == "01 Jan 2024"
        assert results[0]['cover'] == "https://example.com/cover.jpg"
        
        assert results[1]['title'] == "Another Test Book - Another Author"
        assert results[1]['language'] == "Spanish"
        assert results[1]['format'] == "MP3"
        assert results[1]['size'] == "1.01 GBs"

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    @patch('shelfmark.release_sources.audiobookbay.scraper.config.get')
    def test_search_audiobookbay_pagination(self, mock_config_get, mock_get_proxies, mock_get):
        """Test pagination through multiple pages."""
        mock_config_get.return_value = 0.0  # No delay for faster tests
        mock_get_proxies.return_value = {}
        
        # First page response
        mock_response_page1 = Mock()
        mock_response_page1.status_code = 200
        mock_response_page1.url = "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined"
        mock_response_page1.text = SAMPLE_SEARCH_HTML
        
        # Second page response (empty)
        mock_response_page2 = Mock()
        mock_response_page2.status_code = 200
        mock_response_page2.url = "https://audiobookbay.lu/page/2/?s=test&cat=undefined%2Cundefined"
        mock_response_page2.text = EMPTY_SEARCH_HTML
        
        mock_get.side_effect = [mock_response_page1, mock_response_page2]
        
        results = scraper.search_audiobookbay("test", max_pages=2, hostname="audiobookbay.lu")
        
        assert len(results) == 2  # Only from first page
        assert mock_get.call_count == 2

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    @patch('shelfmark.release_sources.audiobookbay.scraper.config.get')
    def test_search_audiobookbay_empty(self, mock_config_get, mock_get_proxies, mock_get):
        """Test search with no results."""
        mock_config_get.return_value = 1.0
        mock_get_proxies.return_value = {}
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined"
        mock_response.text = EMPTY_SEARCH_HTML
        mock_get.return_value = mock_response
        
        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")
        
        assert len(results) == 0

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    @patch('shelfmark.release_sources.audiobookbay.scraper.config.get')
    def test_search_audiobookbay_error_non_200(self, mock_config_get, mock_get_proxies, mock_get):
        """Test error handling for non-200 status code."""
        mock_config_get.return_value = 1.0
        mock_get_proxies.return_value = {}
        
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")
        
        assert len(results) == 0

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    @patch('shelfmark.release_sources.audiobookbay.scraper.config.get')
    def test_search_audiobookbay_redirect_to_homepage(self, mock_config_get, mock_get_proxies, mock_get):
        """Test handling redirect to homepage (blocked/invalid search)."""
        mock_config_get.return_value = 1.0
        mock_get_proxies.return_value = {}
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://audiobookbay.lu"  # Redirected to homepage
        mock_response.text = EMPTY_SEARCH_HTML
        mock_get.return_value = mock_response
        
        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")
        
        assert len(results) == 0

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    @patch('shelfmark.release_sources.audiobookbay.scraper.config.get')
    def test_search_audiobookbay_request_exception(self, mock_config_get, mock_get_proxies, mock_get):
        """Test handling request exceptions."""
        mock_config_get.return_value = 1.0
        mock_get_proxies.return_value = {}
        
        mock_get.side_effect = requests.exceptions.RequestException("Connection error")
        
        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")
        
        assert len(results) == 0

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    @patch('shelfmark.release_sources.audiobookbay.scraper.config.get')
    def test_search_audiobookbay_relative_link(self, mock_config_get, mock_get_proxies, mock_get):
        """Test handling relative links in results."""
        mock_config_get.return_value = 1.0
        mock_get_proxies.return_value = {}
        
        html_with_relative_link = """
        <div class="post">
            <div class="postTitle"><h2><a href="/abss/relative-link/">Test Book</a></h2></div>
            <div class="postInfo">Language: English</div>
            <div class="postContent">
                <p style="text-align:center;">Posted: 01 Jan 2024<br>Format: M4B<br>File Size: 100 MBs</p>
            </div>
        </div>
        """
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined"
        mock_response.text = html_with_relative_link
        mock_get.return_value = mock_response
        
        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")
        
        assert len(results) == 1
        assert results[0]['link'] == "https://audiobookbay.lu/abss/relative-link/"


class TestExtractMagnetLink:
    """Tests for the extract_magnet_link function."""

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    def test_extract_magnet_link_success(self, mock_get_proxies, mock_get):
        """Test successful magnet link extraction."""
        mock_get_proxies.return_value = {}
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_DETAIL_HTML
        mock_get.return_value = mock_response
        
        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/",
            hostname="audiobookbay.lu"
        )
        
        assert magnet_link is not None
        assert magnet_link.startswith("magnet:?xt=urn:btih:")
        assert "ABC123DEF456GHI789JKL012MNO345PQR678STU" in magnet_link
        assert "udp%3A//tracker.openbittorrent.com%3A80" in magnet_link
        assert "http%3A//tracker.example.com%3A8080" in magnet_link

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    def test_extract_magnet_link_fallback(self, mock_get_proxies, mock_get):
        """Test fallback to default trackers when none found."""
        mock_get_proxies.return_value = {}
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = DETAIL_HTML_NO_TRACKERS
        mock_get.return_value = mock_response
        
        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/",
            hostname="audiobookbay.lu"
        )
        
        assert magnet_link is not None
        assert magnet_link.startswith("magnet:?xt=urn:btih:")
        assert "ABC123DEF456GHI789JKL012MNO345PQR678STU" in magnet_link
        # Should contain default trackers
        assert "udp%3A//tracker.openbittorrent.com%3A80" in magnet_link

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    def test_extract_magnet_link_no_info_hash(self, mock_get_proxies, mock_get):
        """Test handling missing info hash."""
        mock_get_proxies.return_value = {}
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html><body></body></html>"
        mock_get.return_value = mock_response
        
        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/",
            hostname="audiobookbay.lu"
        )
        
        assert magnet_link is None

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    def test_extract_magnet_link_non_200(self, mock_get_proxies, mock_get):
        """Test handling non-200 status code."""
        mock_get_proxies.return_value = {}
        
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/",
            hostname="audiobookbay.lu"
        )
        
        assert magnet_link is None

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    def test_extract_magnet_link_request_exception(self, mock_get_proxies, mock_get):
        """Test handling request exceptions."""
        mock_get_proxies.return_value = {}
        
        mock_get.side_effect = requests.exceptions.RequestException("Connection error")
        
        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/",
            hostname="audiobookbay.lu"
        )
        
        assert magnet_link is None

    @patch('shelfmark.release_sources.audiobookbay.scraper.requests.get')
    @patch('shelfmark.release_sources.audiobookbay.scraper.network.get_proxies')
    def test_extract_magnet_link_cleans_info_hash(self, mock_get_proxies, mock_get):
        """Test that info hash whitespace is cleaned."""
        mock_get_proxies.return_value = {}
        
        html_with_whitespace = """
        <html>
        <body>
        <table>
            <tr>
                <td>Info Hash</td>
                <td>ABC 123 DEF 456</td>
            </tr>
        </table>
        </body>
        </html>
        """
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = html_with_whitespace
        mock_get.return_value = mock_response
        
        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/",
            hostname="audiobookbay.lu"
        )
        
        assert magnet_link is not None
        # Info hash should be cleaned (no spaces, uppercase)
        assert "ABC123DEF456" in magnet_link
