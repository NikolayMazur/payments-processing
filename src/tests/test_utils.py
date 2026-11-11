import pytest

from app.utils import mask_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/hook", "https://example.com/hook"),
        ("https://example.com/hook?token=secret&a=1", "https://example.com/hook"),
        ("https://user:password@example.com:8443/hook#frag", "https://example.com:8443/hook"),
        ("http://webhook-sink:8080/h", "http://webhook-sink:8080/h"),
    ],
)
def test_mask_url_drops_credentials_and_query(url: str, expected: str) -> None:
    assert mask_url(url) == expected
