from eggie_api.domain.compose import container_port, exposed_ports


def test_container_port_from_short_mapping():
    assert container_port("8080:80") == 80


def test_container_port_with_host_ip_and_proto():
    assert container_port("127.0.0.1:8080:80/tcp") == 80


def test_container_port_single_value():
    assert container_port("80") == 80


def test_container_port_long_syntax_dict():
    assert container_port({"published": 8080, "target": 80}) == 80


def test_exposed_ports_prefers_ports_then_expose():
    assert exposed_ports({"ports": ["8080:80"]}) == [80]
    assert exposed_ports({"expose": [9000]}) == [9000]
    assert exposed_ports({}) == []
