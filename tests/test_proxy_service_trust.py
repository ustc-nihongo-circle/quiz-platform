import socket
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from quiz.rate_limits import RateLimitUnavailable, _proxy_addresses, client_ip


def test_only_resolved_gateway_can_assert_client_address(settings):
    settings.QUIZ_TRUSTED_PROXY_CIDRS = []
    settings.QUIZ_TRUSTED_PROXY_HOSTS = ["gateway"]
    _proxy_addresses.cache_clear()
    result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.20", 0))]
    factory = RequestFactory()
    with patch("quiz.rate_limits.socket.getaddrinfo", return_value=result) as resolve:
        trusted = factory.get("/", REMOTE_ADDR="192.0.2.20", HTTP_X_REAL_IP="198.51.100.4")
        untrusted = factory.get("/", REMOTE_ADDR="192.0.2.21", HTTP_X_REAL_IP="198.51.100.4")
        assert client_ip(trusted) == "198.51.100.4"
        assert client_ip(untrusted) == "192.0.2.21"
        assert resolve.call_count == 1
    _proxy_addresses.cache_clear()


def test_gateway_address_refreshes_and_dns_failure_does_not_accept_a_claim(settings):
    settings.QUIZ_TRUSTED_PROXY_CIDRS = []
    settings.QUIZ_TRUSTED_PROXY_HOSTS = ["gateway"]
    _proxy_addresses.cache_clear()
    request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.20", HTTP_X_REAL_IP="198.51.100.4")
    with patch("quiz.rate_limits.socket.getaddrinfo", side_effect=socket.gaierror):
        with pytest.raises(RateLimitUnavailable):
            client_ip(request)
    first = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.20", 0))]
    second = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.30", 0))]
    with patch("quiz.rate_limits.socket.getaddrinfo", side_effect=[first, second]), \
            patch("quiz.rate_limits.time.monotonic", side_effect=[0, 31]):
        assert client_ip(request) == "198.51.100.4"
        assert client_ip(request) == "192.0.2.20"
    _proxy_addresses.cache_clear()
