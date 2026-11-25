import pytest

from wanview.domain.status import LineStatus, RouterModel
from wanview.speedport.varlist import VarList


def status(json: str) -> LineStatus:
    return LineStatus.from_vars(VarList.from_json(json))


def test_parses_status_fields() -> None:
    parsed = status("""
    [{"varid":"onlinestatus","varvalue":"online"},
     {"varid":"dsl_link_status","varvalue":"online"},
     {"varid":"dsl_downstream","varvalue":"90000000"},
     {"varid":"dsl_upstream","varvalue":"32000000"},
     {"varid":"inet_download","varvalue":"84716000"},
     {"varid":"inet_upload","varvalue":"30121000"},
     {"varid":"inet_uptime","varvalue":"2026-10-29 04:05:58"},
     {"varid":"use_lte","varvalue":"1"},
     {"varid":"use_bonding","varvalue":"0"},
     {"varid":"ex5g_signal_5g","varvalue":"-102.0"},
     {"varid":"ex5g_freq_5g","varvalue":"NR2100"},
     {"varid":"ex5g_signal_lte","varvalue":"0.0"},
     {"varid":"ex5g_freq_lte","varvalue":""},
     {"varid":"serial_number","varvalue":"SN-123"}]
    """)
    assert parsed.online_status == "online"
    assert parsed.dsl_downstream == 90_000_000
    assert parsed.internet_upload == 30_121_000
    assert parsed.online_since == "2026-10-29 04:05:58"
    assert parsed.uses_mobile
    assert not parsed.uses_bonding
    assert parsed.nr_signal == -102.0
    assert parsed.nr_band == "NR2100"
    assert parsed.lte_signal is None
    assert parsed.lte_band is None
    assert parsed.firmware_version is None
    assert parsed.serial_number == "SN-123"


def test_empty_serial_number_is_no_value() -> None:
    assert status('[{"varid":"serial_number","varvalue":""}]').serial_number is None


@pytest.mark.parametrize(("use_lte", "model_name", "expected"),
                         [("1", "5G-EmpfaengerSE", True), ("1", "", False), ("0", "5G-EmpfaengerSE", False)])
def test_receiver_needs_hybrid_and_model_name(use_lte: str, model_name: str, expected: bool) -> None:
    parsed = status(f'[{{"varid":"use_lte","varvalue":"{use_lte}"}},{{"varid":"ex5g_model_name","varvalue":"{model_name}"}}]')
    assert parsed.has_receiver == expected


def sync(link: str, rate: str) -> LineStatus:
    return status(f'[{{"varid":"dsl_link_status","varvalue":"{link}"}},'
                  f'{{"varid":"dsl_downstream","varvalue":"{rate}"}},{{"varid":"dsl_upstream","varvalue":"{rate}"}}]')


def test_zero_sync_while_online_is_no_value() -> None:
    glitch = sync("online", "0")
    assert glitch.dsl_downstream is None
    assert glitch.dsl_upstream is None


def test_zero_sync_while_link_down_is_kept() -> None:
    assert sync("offline", "0").dsl_downstream == 0


@pytest.mark.parametrize("title", ["Speedport Smart 4 - Konfigurationsprogramm",
                                   "Speedport Smart 4 Typ A - Konfigurationsprogramm",
                                   "Speedport Smart 4 Typ B - Configuration",
                                   "Speedport Smart 4 Plus - Konfigurationsprogramm"])
def test_smart4_from_status_title(title: str) -> None:
    assert RouterModel.from_status_title(title) == RouterModel("Speedport Smart 4", supported=True)


def test_other_speedport_is_unsupported() -> None:
    assert RouterModel.from_status_title("Speedport Smart 3 - Konfigurationsprogramm") == RouterModel(
        "Speedport Smart 3", supported=False)


def test_html_title_fallback() -> None:
    assert RouterModel.from_html("<html><head><TITLE>\n FRITZ!Box \n</TITLE></head>").name == "FRITZ!Box"
    assert RouterModel.from_html("<html>no title</html>").name == "unknown device"
