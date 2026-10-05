import json


def manifest(env, agent_id, setup=True):
    folder = env.config.agents_dir / agent_id
    folder.mkdir(parents=True)
    body = {"home": f".{agent_id}", **({"setup": {"run": "true"}} if setup else {})}
    (folder / "agent.json").write_text(json.dumps(body))


def runner_status(env, agents):
    env.config.agent_status_dir.mkdir(exist_ok=True)
    (env.config.agent_status_dir / "status.json").write_text(json.dumps({"generation": 1, "agents": agents}))


def counter(env, *parts):
    path = env.config.agent_status_dir.joinpath(*parts)
    return int(path.read_text()) if path.exists() else 0


def test_status_gives_the_runners_view_and_asks_for_a_fresh_one(env):
    runner_status(env, {"codex": {"connected": True, "setup": "ready", "setup_generation": 3}})
    assert env.client.get("/agents/status").json() == {"agents": {"codex": {"connected": True, "setup": "ready"}}}
    env.client.get("/agents/status")
    assert counter(env, "check") == 2


def test_a_missing_or_garbled_status_reads_as_no_agents(env):
    assert env.client.get("/agents/status").json() == {"agents": {}}
    (env.config.agent_status_dir / "status.json").write_text('{"agents": {"codex": ')
    assert env.client.get("/agents/status").json() == {"agents": {}}
    runner_status(env, {"codex": "yes", "cursor": {"connected": "maybe", "setup": "exploded"}})
    assert env.client.get("/agents/status").json() == {"agents": {"cursor": {"connected": False, "setup": None}}}


def test_setup_needs_an_agent_that_has_one(env):
    manifest(env, "claude-code", setup=False)
    for agent_id in ("nope", "claude-code"):
        response = env.client.post(f"/agents/{agent_id}/setup")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "agent_not_found"


def test_setup_is_requested_only_when_nothing_is_ready_running_or_connected(env):
    manifest(env, "codex")
    for state, connected in (("ready", False), ("installing", False), (None, True)):
        runner_status(env, {"codex": {"connected": connected, "setup": state}})
        assert env.client.post("/agents/codex/setup").json() == {"requested": False}
    assert counter(env, "setup", "codex") == 0

    runner_status(env, {"codex": {"connected": False, "setup": "failed"}})
    assert env.client.post("/agents/codex/setup").json() == {"requested": True}
    assert env.client.post("/agents/codex/setup").json() == {"requested": True}
    assert counter(env, "setup", "codex") == 2
    assert counter(env, "check") >= 2
