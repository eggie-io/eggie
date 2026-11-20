from __future__ import annotations

import yaml

from eggie_api.domain.edge import Domain, Pair, ProjectEdge, build_edge
from eggie_api.services.edge import Edge


class _Certs:
    def __init__(self, pairs):
        self.pairs = pairs

    def installed(self):
        return self.pairs


PAIR = Pair("i1", "instance", "/e/certs/i1/cert.pem", "/e/certs/i1/key.pem", ("cloud.test",))
DOMAIN = Domain("shop.example.com", "blog", True, "active", None)
PROJECT = ProjectEdge("blog.cloud.test", "blog--web")


def _edge(tmp_path, pairs=(PAIR,), domains=(), projects=None):
    asked = []

    def load(ids):
        asked.append(set(ids))
        return projects or {}

    edge = Edge(tmp_path / "edge", _Certs(list(pairs)), lambda: list(domains), load)
    return edge, asked


def test_the_file_is_replaced_by_one_rename_and_untouched_when_nothing_changed(tmp_path):
    edge, _ = _edge(tmp_path)
    assert edge.write() is True and edge.write() is False
    directory = tmp_path / "edge"
    assert not (directory / "edge.yml.new").exists()
    inode = (directory / "edge.yml").stat().st_ino
    edge.write()
    assert (directory / "edge.yml").stat().st_ino == inode


def test_the_file_is_the_built_mapping_and_follows_a_changed_input(tmp_path):
    domains = []
    certs = _Certs([PAIR])
    edge = Edge(tmp_path / "edge", certs, lambda: domains, lambda ids: {"blog": PROJECT})
    edge.write()
    written = yaml.safe_load((tmp_path / "edge" / "edge.yml").read_text())
    assert written == build_edge([PAIR], [], {})
    domains.append(DOMAIN)
    assert edge.write() is True
    written = yaml.safe_load((tmp_path / "edge" / "edge.yml").read_text())
    assert written == build_edge([PAIR], [DOMAIN], {"blog": PROJECT})


def test_an_empty_mapping_is_still_a_file_for_traefik(tmp_path):
    edge, _ = _edge(tmp_path, pairs=())
    assert edge.write() is True
    assert yaml.safe_load((tmp_path / "edge" / "edge.yml").read_text()) in ({}, None)
    assert (tmp_path / "edge" / "edge.yml").read_text().strip() == "{}"


def test_projects_are_asked_only_for_those_with_domains(tmp_path):
    edge, asked = _edge(tmp_path)
    edge.write()
    assert asked == []
    edge, asked = _edge(tmp_path, domains=[DOMAIN], projects={"blog": PROJECT})
    edge.write()
    assert asked == [{"blog"}]


def test_a_project_without_a_row_a_web_or_a_readable_definition_is_skipped():
    from eggie_api.domain.detect import WebSpec
    from eggie_api.errors import Invalid
    from eggie_api.wiring import _project_edges

    class Loader:
        def load(self, pid):
            if pid == "broken":
                raise Invalid("invalid_project", "bad")
            webs = [] if pid == "static" else [WebSpec("web", 80)]
            return type("P", (), {"webs": webs})()

    rows = {"blog": {"domain": "c.test"}, "broken": {"domain": "c.test"},
            "static": {"domain": "c.test"}}
    got = _project_edges(Loader(), rows)({"blog", "broken", "static", "gone"})
    assert got == {"blog": ProjectEdge("blog.c.test", "blog--web")}
