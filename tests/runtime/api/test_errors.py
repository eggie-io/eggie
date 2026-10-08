from eggie_api.errors import Conflict, EggieError, NotFound


def test_subclasses_carry_the_wire_code_and_message():
    e = NotFound("project_not_found", "no project with id 'x'")
    assert isinstance(e, EggieError)
    assert (e.code, e.message, str(e)) == ("project_not_found", "no project with id 'x'",
                                           "no project with id 'x'")
    assert e.extra == {}


def test_extra_fields_ride_along():
    e = Conflict("offset_mismatch", "the upload is at a different offset", extra={"offset": 7})
    assert e.extra == {"offset": 7}


def test_error_body_merges_extra(env):
    # A second chunk at the wrong offset answers with the offset the client
    # must resume from -- the one field the host's uploader reads.
    env.client.post("/projects", json={"id": "blog"})
    up = env.client.post("/projects/blog/uploads",
                         json={"path": "a.txt", "size": 4}).json()
    r = env.client.patch(f"/uploads/{up['upload_id']}", content=b"ab",
                         headers={"Upload-Offset": "3"})
    assert r.status_code == 409
    assert r.json()["error"] == {"code": "offset_mismatch",
                                 "message": "the upload is at a different offset",
                                 "offset": 0}
