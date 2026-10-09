import asyncio
import threading

import httpx

from eggie_api.infra import files

from tests.runtime.api.conftest import AUTH


def test_the_api_keeps_answering_while_an_archive_unpacks(env, monkeypatch):
    # Unpacking a large project takes minutes inside the VM. Run on the event
    # loop, it left every other request -- the console's included -- hanging.
    env.client.post("/projects", json={"id": "big"})
    unpacking = threading.Event()
    release = threading.Event()
    released_by_health = []

    # Blocks until the test lets it go. On the event loop it can only time out,
    # because nothing else gets to run and set `release`.
    def slow_extract(archive, dest):
        unpacking.set()
        released_by_health.append(release.wait(5))

    monkeypatch.setattr(files, "extract_archive", slow_extract)

    async def scenario():
        transport = httpx.ASGITransport(app=env.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api", headers=AUTH) as client:
            upload = asyncio.create_task(client.post("/projects/big/files", content=b"tar"))
            while not unpacking.is_set():
                await asyncio.sleep(0.01)
            try:
                health = await asyncio.wait_for(client.get("/health"), timeout=2)
            finally:
                release.set()
            await upload
            return health.status_code

    assert asyncio.run(scenario()) == 200
    assert released_by_health == [True]

