"""Golden Config export cleaning and secret masking. Pure Python plus an optional
dispatcher test that runs where nornir-nautobot is installed."""

import json
import logging
import re
from pathlib import Path

import pytest

from nautobot_ssot_lantronix.golden_config.export import (
    EXPORT_COMMAND,
    SECRET_RULES,
    clean_export,
    mask_secrets,
)

FIXTURES = Path(__file__).parent / "fixtures"
with open(FIXTURES / "itmgos_export_9.7.0.0R21.txt", newline="") as fh:
    RAW = fh.read()
ORACLE = json.loads((FIXTURES / "itmgos_export_secrets.json").read_text())
COMMAND = re.compile(r"^(?:set|admin) ", re.M)


@pytest.fixture(scope="module")
def cleaned():
    return clean_export(RAW)


@pytest.mark.parametrize("secret", ORACLE["fail"])
def test_no_secret_survives(cleaned, secret):
    assert secret not in cleaned


@pytest.mark.parametrize("masked", ORACLE["pass"])
def test_masked_lines_are_present(cleaned, masked):
    assert masked in cleaned


def test_secrets_are_present_in_the_raw_fixture():
    # Guards against a fixture that would let the masking tests pass vacuously.
    assert all(secret in RAW for secret in ORACLE["fail"])


def test_chatter_and_prompt_are_removed(cleaned):
    lines = cleaned.splitlines()
    assert lines[0] == "set network port 1 state dhcp"
    assert lines[-1].startswith("admin web iface ")
    assert "\r" not in cleaned
    assert not any(
        line in ("yes",) or line.startswith(("One moment", "[SLC9016-LAB]>")) for line in lines
    )


def test_no_command_line_is_lost(cleaned):
    assert len(COMMAND.findall(cleaned)) == len(COMMAND.findall(RAW.replace("\r", "")))


def test_masking_is_idempotent(cleaned):
    assert mask_secrets(cleaned) == cleaned


def test_netmiko_stripped_output_cleans_the_same(cleaned):
    stripped = RAW.split("\n", 1)[1].rsplit("\n", 1)[0]  # no "yes" echo, no trailing prompt
    assert clean_export(stripped) == cleaned


def test_empty_export_gives_empty_config():
    assert clean_export("yes\r\nOne moment while the file is generated...\r\n[SLC-A]> ") == ""


def test_rules_work_the_way_netutils_applies_them():
    # Golden Config "config replace" rules run through re.sub(..., flags=re.MULTILINE) on the
    # whole config; seeding SECRET_RULES there must give the same result.
    raw = RAW.replace("\r\n", "\n")
    for _, regex, repl in SECRET_RULES:
        raw = re.sub(regex, repl, raw, flags=re.MULTILINE)
    for secret in ORACLE["fail"]:
        assert secret not in raw


class FakeConnection:
    """Replays the export over raw channel reads, the way the dispatcher talks to the device."""

    RETURN = "\n"

    def __init__(self, outputs):
        self.outputs, self.sent, self.patterns = list(outputs), [], []

    def find_prompt(self):
        return "[SLC9016-LAB]>"

    def write_channel(self, data):
        self.sent.append(data)

    def read_until_pattern(self, pattern, read_timeout=None):
        self.patterns.append(pattern)
        return self.outputs.pop(0)


class FakeHost:
    def __init__(self, connection):
        self.connection, self.name, self.platform = connection, "SLC-A", "lantronix_itmgos"

    def get_connection(self, plugin, config):
        assert plugin == "netmiko"
        return self.connection


class FakeTask:
    def __init__(self, connection):
        self.host = FakeHost(connection)
        self.nornir = type("N", (), {"config": None})()


class Obj:
    name = "SLC-A"


def test_dispatcher_runs_the_export_and_answers_the_prompt():
    dispatcher = pytest.importorskip("nautobot_ssot_lantronix.golden_config.dispatcher")
    first = (
        "The configuration will be output (including passwords in cleartext).\n"
        "Do you want to continue? [no] "
    )
    connection = FakeConnection([first, RAW])
    result = dispatcher.NetmikoLantronixItmgos.get_config(
        FakeTask(connection), logging.getLogger("t"), Obj(), None, [], []
    )
    assert connection.sent == [EXPORT_COMMAND + "\n", "yes\n"]
    assert "continue" in connection.patterns[0]  # waits for the confirmation before "yes"
    assert connection.patterns[1] == re.escape("[SLC9016-LAB]>")  # then reads to the prompt
    config = result.result["config"]
    assert all(secret not in config for secret in ORACLE["fail"])


def test_dispatcher_refuses_an_empty_export():
    dispatcher = pytest.importorskip("nautobot_ssot_lantronix.golden_config.dispatcher")
    from nornir_nautobot.exceptions import NornirNautobotException

    connection = FakeConnection(["Do you want to continue? [no] ", "yes\n[SLC-A]> "])
    with pytest.raises(NornirNautobotException):
        dispatcher.NetmikoLantronixItmgos.get_config(
            FakeTask(connection), logging.getLogger("t"), Obj(), None, [], []
        )


def _crlf_body():
    return RAW.replace("\r\n", "\n").replace("\r", "\n")


@pytest.mark.parametrize(
    "ending", ["\r\r\n", "\n\r", "\r", "\r\n"], ids=["crcrlf", "lfcr", "cr", "crlf"]
)
@pytest.mark.parametrize("secret", ORACLE["fail"])
def test_no_secret_survives_any_line_ending(ending, secret):
    raw = _crlf_body().replace("\n", ending)
    assert secret not in clean_export(raw)


@pytest.mark.parametrize("secret", ORACLE["fail"])
def test_no_secret_survives_ansi_sequences(secret):
    raw = "\x1b[?2004h" + _crlf_body().replace("\n", "\x1b[K\r\n") + "\x1b[0m"
    out = clean_export(raw)
    assert secret not in out and "\x1b" not in out


def test_unmasked_prompted_secret_fails_closed():
    from nautobot_ssot_lantronix.golden_config.export import UnmaskedSecretError

    # Only one line follows the prompted command, so the rule can't mask a value and its copy.
    with pytest.raises(UnmaskedSecretError):
        mask_secrets("set tacacs+ secret\nhunter2\n")


@pytest.mark.parametrize("secret", ORACLE["fail"])
def test_no_secret_survives_osc_and_control_characters(secret):
    body = _crlf_body().replace("\n", "\x1b]0;slc-title\x07\x00\r\n")
    out = clean_export("\x1b]2;title\x1b\\" + body)
    assert secret not in out and "\x1b" not in out and "\x00" not in out


def test_misaligned_prompted_secret_fails_closed():
    from nautobot_ssot_lantronix.golden_config.export import UnmaskedSecretError

    # A stray blank line between the command and its value: value and confirmation don't pair.
    with pytest.raises(UnmaskedSecretError):
        mask_secrets("set tacacs+ secret\n\nhunter2-long\nhunter2-long\n")


def test_loose_recognizer_catches_lines_the_rule_misses():
    from nautobot_ssot_lantronix.golden_config.export import UnmaskedSecretError

    # Leading whitespace defeats the anchored mask rule; the guard still refuses the export.
    with pytest.raises(UnmaskedSecretError):
        mask_secrets("  set tacacs+ secret\nhunter2-long\nhunter2-long\n")


def test_unset_prompted_secret_passes():
    out = mask_secrets("set tacacs+ secret\n\n\nset ntp enable\n")
    assert out.splitlines()[1:3] == ["<secret hidden>", "<secret hidden>"]


def test_remasking_a_clean_export_passes_the_guard():
    once = clean_export(RAW)
    assert mask_secrets(once) == once


@pytest.mark.parametrize(
    "command", ["set site chapsecret dialup1", "set site dialoutpassword dialup1"]
)
def test_site_secrets_misaligned_fail_closed(command):
    from nautobot_ssot_lantronix.golden_config.export import UnmaskedSecretError

    with pytest.raises(UnmaskedSecretError):
        mask_secrets(f"{command}\n\nsite-secret-long\nsite-secret-long\n")


@pytest.mark.parametrize(
    "command", ["set site chapsecret dialup1", "set site dialoutpassword dialup1"]
)
def test_site_secrets_truncated_fail_closed(command):
    from nautobot_ssot_lantronix.golden_config.export import UnmaskedSecretError

    with pytest.raises(UnmaskedSecretError):
        mask_secrets(f"{command}\nsite-secret-long")


@pytest.mark.parametrize(
    "command", ["set site chapsecret dialup1", "set site dialoutpassword dialup1"]
)
def test_site_secrets_masked(command):
    out = mask_secrets(f"{command}\nsite-secret-long\nsite-secret-long\nset ntp enable\n")
    assert "site-secret-long" not in out
