"""Static check: time and bot switches stay available in the compact mode (report 0110)."""

import re
from html.parser import HTMLParser
from pathlib import Path

INDEX = Path(__file__).parents[2] / "quoridor/analyzer/static/index.html"


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []  # (tag, id, classes, ancestors_classes_snapshot)
        self.found = []  # (id, own classes, all ancestor classes)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag not in ("meta", "link", "br", "img", "input"):
            self.stack.append((tag, a.get("id"), cls))
        if a.get("id"):
            anc = [c for _, _, cl in self.stack[:-1] for c in cl]
            self.found.append((a["id"], cls, anc))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _parse():
    t = _Tree()
    t.feed(INDEX.read_text(encoding="utf-8"))
    return t.found


def test_mode_and_bot_switches_are_not_full_only():
    found = _parse()
    ids = [f for f in found if re.match(r"(mode|bot)_", f[0])]
    assert {f[0] for f in ids} >= {
        "mode_fast",
        "mode_normal",
        "mode_deep",
        "bot_mcts",
        "bot_v5",
        "bot_v4",
    }
    for id_, own, ancestors in ids:
        assert "full-only" not in own, id_
        assert "full-only" not in ancestors, id_


def test_mode_and_bot_containers_are_not_full_only():
    text = INDEX.read_text(encoding="utf-8")
    # the .seg container is the nearest <span> before its first button
    for key in ("mode_fast", "bot_mcts"):
        pos = text.index(f'id="{key}"')
        open_tag = text.rindex("<span", 0, pos)
        tag = text[open_tag : text.index(">", open_tag)]
        assert "full-only" not in tag, tag


def test_new_board_button_available_in_compact():
    found = {f[0]: f for f in _parse()}
    _, own, ancestors = found["bNew"]
    assert "full-only" not in own and "full-only" not in ancestors
    assert "newAsk" in found


def test_board_enlarged_in_compact_without_hardcoded_pick():
    """Report 0121: the compact board is cropped via viewBox, clicks use getScreenCTM."""
    js = (INDEX.parent / "app.js").read_text(encoding="utf-8")
    css = (INDEX.parent / "app.css").read_text(encoding="utf-8")
    assert 'svg.setAttribute("viewBox", on ?' in js
    assert "getScreenCTM().inverse()" in js
    assert "body.compact svg#board" in css
    assert "aspect-ratio:1/1" in css
    assert 'id="evalbar"' in INDEX.read_text(encoding="utf-8")
