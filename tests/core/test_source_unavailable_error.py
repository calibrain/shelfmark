from shelfmark.release_sources import SourceUnavailableError


def test_a_credential_query_parameter_is_masked_in_the_message():
    error = SourceUnavailableError(
        "could not reach it: http://host/api?t=book&apikey=SECRETKEY123&q=x"
    )

    assert "SECRETKEY123" not in str(error)
    assert "apikey=(removed)" in str(error)


def test_userinfo_is_masked_in_the_message():
    error = SourceUnavailableError("could not reach http://user:hunter2@host:9117/api")

    assert "hunter2" not in str(error)


def test_a_plain_message_is_unchanged():
    message = "every indexer is disabled by Prowlarr after recent failures (until 12:00)"

    assert str(SourceUnavailableError(message)) == message
