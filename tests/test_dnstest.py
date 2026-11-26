"""DnsTester against a local stub DNS server (UDP on localhost, no internet)."""

import asyncio

import dns.message
import dns.rcode
import dns.rrset
import pytest

from wanview.domain.samples import DnsOutcome
from wanview.dnstest import DnsTester

TIMEOUT_SECONDS = 0.3


class StubDns(asyncio.DatagramProtocol):
    """Answers "ok.test" with an address, "missing.test" with NXDOMAIN, ignores "slow.test"."""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        query = dns.message.from_wire(data)
        name = query.question[0].name.to_text()
        if name == "slow.test.":
            return
        response = dns.message.make_response(query)
        if name == "missing.test.":
            response.set_rcode(dns.rcode.NXDOMAIN)
        else:
            response.answer.append(dns.rrset.from_text(name, 60, "IN", "A", "192.0.2.1"))
        self.transport.sendto(response.to_wire(), address)


@pytest.fixture
async def stub_port():
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(StubDns, local_addr=("127.0.0.1", 0))
    yield transport.get_extra_info("sockname")[1]
    transport.close()


async def test_outcomes(stub_port: int) -> None:
    tester = DnsTester(port=stub_port, timeout_seconds=TIMEOUT_SECONDS)
    results = {r.hostname: r for r in await tester.run(["127.0.0.1"], ["ok.test", "missing.test", "slow.test"])}
    assert results["ok.test"].outcome == DnsOutcome.OK and results["ok.test"].latency_ms is not None
    assert results["missing.test"].outcome == DnsOutcome.NXDOMAIN
    assert results["slow.test"].outcome == DnsOutcome.TIMEOUT and results["slow.test"].latency_ms is None
