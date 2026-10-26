from eggie_api.services.ratelimit import KeyedWindow


def test_a_full_set_of_senders_refuses_new_ones_until_one_goes_idle():
    now = [0.0]
    window = KeyedWindow(10, 60.0, lambda: now[0], max_keys=2)
    assert window.take("a") and window.take("b")

    assert not window.take("c")
    assert window.take("a"), "a tracked sender keeps its own limit"

    now[0] = 61.0
    assert window.take("c")
