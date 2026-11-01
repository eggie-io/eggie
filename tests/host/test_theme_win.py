import threading

from host.providers.theme_win import ThemeWatch


def test_every_subscriber_hears_every_change_even_after_one_fails():
    subscribed, done = threading.Event(), threading.Event()
    changes = [2]

    def wait():
        subscribed.wait(5)
        if changes[0] == 0:
            done.set()
            raise OSError("watch over")
        changes[0] -= 1

    heard = []

    def broken():
        heard.append("tray")
        raise RuntimeError("tray gone")

    watch = ThemeWatch(open_waiter=lambda: wait)
    watch.subscribe(broken)
    watch.subscribe(lambda: heard.append("title bar"))
    subscribed.set()
    assert done.wait(5)
    assert heard == ["tray", "title bar", "tray", "title bar"]
