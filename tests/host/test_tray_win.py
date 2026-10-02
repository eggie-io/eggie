from types import SimpleNamespace

from host.providers.tray_win import session_end_aware

SHUTDOWN, USER = "WindowsShutDown", "UserClosing"


def _wrapped():
    seen, ended = [], []

    def original(form, sender, args):
        seen.append(args.CloseReason)

    return session_end_aware(original, (SHUTDOWN,), lambda: ended.append(True)), seen, ended


def test_a_session_end_close_is_announced_before_pywebview_decides():
    on_closing, seen, ended = _wrapped()
    on_closing(object(), None, SimpleNamespace(CloseReason=SHUTDOWN))
    assert ended == [True] and seen == [SHUTDOWN]


def test_a_title_bar_close_is_not_a_session_end():
    on_closing, seen, ended = _wrapped()
    on_closing(object(), None, SimpleNamespace(CloseReason=USER))
    assert ended == [] and seen == [USER]
