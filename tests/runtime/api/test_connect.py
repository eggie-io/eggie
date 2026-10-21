import json

from eggie_api.infra import connect


def _write(tmp_path, value):
    path = tmp_path / "connect.json"
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return path


def test_lima_with_a_user_gets_ssh_details(tmp_path):
    facts = connect.facts(_write(tmp_path, {"vm": "lima", "user": "ada"}))
    assert facts == {"vm": "lima", "ssh": {"host": "127.0.0.1", "port": 39022,
                                           "user": "ada", "key_file": "~/.lima/_config/user"}}


def test_lima_without_a_user_has_no_ssh_details(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "lima", "user": ""}))["ssh"] is None


def test_wsl_never_offers_ssh(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "wsl", "user": "ada"})) == {"vm": "wsl", "ssh": None}


def test_a_missing_file_reads_as_an_unknown_vm(tmp_path):
    assert connect.facts(tmp_path / "absent.json") == {"vm": "other", "ssh": None}


def test_a_corrupt_file_reads_as_an_unknown_vm(tmp_path):
    assert connect.facts(_write(tmp_path, "{not json")) == {"vm": "other", "ssh": None}


def test_an_unknown_vm_kind_is_not_passed_through(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "hyperv", "user": "ada"})) == {"vm": "other", "ssh": None}


def test_the_route_is_reachable_on_the_token_mount(env):
    env.config.connect_path.write_text(json.dumps({"vm": "wsl", "user": "ada"}))
    assert env.client.get("/connect").json() == {"vm": "wsl", "ssh": None}


def test_a_site_tells_agents_its_host_on_the_standard_ssh_port_with_no_key_file(tmp_path):
    facts = connect.facts(_write(tmp_path, {"vm": "site", "user": "ada"}), site_host="foo.eggie.io")
    assert facts == {"vm": "site", "ssh": {"host": "foo.eggie.io", "port": 22,
                                           "user": "ada", "key_file": None}}


def test_a_site_record_without_a_site_host_has_no_ssh_facts(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "site", "user": "ada"})) == {"vm": "site", "ssh": None}


def test_a_site_prefers_the_login_the_service_reports_over_the_recorded_one(tmp_path):
    facts = connect.facts(_write(tmp_path, {"vm": "site", "user": "root"}),
                          site_host="foo.eggie.io", site_user="ada")
    assert facts["ssh"]["user"] == "ada"
