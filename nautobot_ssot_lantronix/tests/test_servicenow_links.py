"""Pure-Python tests for the ServiceNow console-path link builder."""

import logging

import pytest

from nautobot_ssot_lantronix.servicenow.links import (
    REL_TYPE,
    ConsoleCable,
    build_links,
    link_id,
    port_number,
    stale_links,
)


def cable(server="SLC9016-16E-A", port="Port-14", attached="switch-01"):
    return ConsoleCable(server, port, attached)


def test_default_direction_makes_the_attached_device_the_parent():
    [link] = build_links([cable()])
    assert link == {
        "parent_name": "switch-01",
        "child_name": "SLC9016-16E-A",
        "relationship_type": REL_TYPE,
        "port": "14",
    }


def test_console_server_direction_flips_parent_and_child():
    [link] = build_links([cable()], parent="console_server")
    assert (link["parent_name"], link["child_name"]) == ("SLC9016-16E-A", "switch-01")


def test_unknown_direction_is_rejected():
    with pytest.raises(ValueError):
        build_links([cable()], parent="sideways")


def test_two_console_ports_to_one_server_give_one_link_with_the_lowest_port(caplog):
    with caplog.at_level(logging.INFO):
        links = build_links(
            [cable(port="Port-17"), cable(port="Port-03")], logger=logging.getLogger("t")
        )
    assert [link["port"] for link in links] == ["3"]
    assert "Port-03 is written, not written: Port-17" in caplog.text


def test_links_to_different_servers_stay_separate_and_sorted():
    links = build_links([cable(server="SLC-B", port="Port-02"), cable(server="SLC-A")])
    assert [(link["child_name"], link["port"]) for link in links] == [
        ("SLC-A", "14"),
        ("SLC-B", "2"),
    ]


def test_self_link_is_skipped(caplog):
    with caplog.at_level(logging.WARNING):
        links = build_links([cable(attached="SLC9016-16E-A")], logger=logging.getLogger("t"))
    assert links == [] and "self-cabled" in caplog.text


@pytest.mark.parametrize(
    ("name", "number"), [("Port-14", 14), ("Port-01", 1), ("14", 14), ("Console", None), ("", None)]
)
def test_port_number(name, number):
    assert port_number(name) == number


def test_port_without_a_number_is_written_empty():
    [link] = build_links([cable(port="Console")])
    assert link["port"] == ""


def test_stale_links_are_target_only_ids():
    keep = link_id(build_links([cable()])[0])
    gone = link_id(build_links([cable(attached="old-router")])[0])
    assert stale_links([keep], [keep, gone]) == [gone]
    assert stale_links([keep], [keep]) == []
