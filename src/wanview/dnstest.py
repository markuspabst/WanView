"""DNS test: how fast configured DNS servers answer, queried directly over UDP (one A
query per server and hostname, no retries, no cache), so each server is measured on
its own instead of through the host's resolver.

Thread-safety: use from one event loop.
"""

import asyncio
import time
from datetime import UTC, datetime

import dns.asyncquery
import dns.exception
import dns.message
import dns.rcode

from wanview.domain.samples import DnsOutcome, DnsSample

DNS_PORT = 53
_DEFAULT_TIMEOUT_SECONDS = 2.0
_MS_PER_SECOND = 1000


class DnsTester:
    def __init__(self, port: int = DNS_PORT, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._port = port
        self._timeout = timeout_seconds

    async def run(self, servers: list[str], hostnames: list[str]) -> list[DnsSample]:
        """`servers` are IP addresses. All queries run concurrently; results keep the
        order servers × hostnames."""
        now = datetime.now(UTC)
        return list(await asyncio.gather(*(self._query(now, server, hostname)
                                           for server in servers for hostname in hostnames)))

    async def _query(self, now: datetime, server: str, hostname: str) -> DnsSample:
        query = dns.message.make_query(hostname, "A")
        start = time.perf_counter()
        try:
            response = await dns.asyncquery.udp(query, server, timeout=self._timeout, port=self._port)
        except dns.exception.Timeout:
            return DnsSample(now, server, hostname, None, DnsOutcome.TIMEOUT)
        except (OSError, dns.exception.DNSException):
            return DnsSample(now, server, hostname, None, DnsOutcome.ERROR)
        latency = (time.perf_counter() - start) * _MS_PER_SECOND
        rcode = response.rcode()
        if rcode == dns.rcode.NOERROR:
            outcome = DnsOutcome.OK
        elif rcode == dns.rcode.NXDOMAIN:
            outcome = DnsOutcome.NXDOMAIN
        else:
            outcome = DnsOutcome.ERROR
        return DnsSample(now, server, hostname, latency, outcome)
