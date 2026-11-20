from paperplane.config import ChannelConfig
from paperplane.core import safe_name
from paperplane.matcher import Matcher
from paperplane.store import CachedMessage


def msg(i, text="", media=None, grouped=None, reply=None, name=None, size=100):
    return CachedMessage(1, i, grouped, 0, text, name, media, size if media else None, reply)


def ids(cfg, msgs):
    return [m.msg_id for m in Matcher(cfg).select(msgs)]


def test_caption_and_filename():
    cfg = ChannelConfig(chat="x", include=["remux"])
    msgs = [msg(1, "普通视频", "video"), msg(2, "4K REMUX", "video"), msg(3, "", "document", name="a.remux.mkv")]
    assert ids(cfg, msgs) == [2, 3]


def test_album_shares_caption():
    cfg = ChannelConfig(chat="x", include=["原盘"], media={"photo", "video"})
    msgs = [msg(1, "原盘合集", "photo", grouped=9), msg(2, "", "video", grouped=9), msg(3, "", "photo", grouped=8)]
    assert ids(cfg, msgs) == [1, 2]


def test_reply_and_context():
    cfg = ChannelConfig(chat="x", include=["关键词"], context_before=1, follow_replies=True)
    msgs = [msg(1, "关键词 介绍"), msg(2, "", "video"),          # 前一条文字命中
            msg(3, "无关"), msg(4, "", "video"),                  # 不命中
            msg(5, "", "document", reply=1)]                       # 回复命中
    assert ids(cfg, msgs) == [2, 5]


def test_exclude_type_and_size():
    cfg = ChannelConfig(chat="x", include=["a"], exclude=["预告"], media={"video"}, min_size=50)
    msgs = [msg(1, "a 预告", "video"), msg(2, "a", "photo"), msg(3, "a", "video", size=10), msg(4, "a", "video")]
    assert ids(cfg, msgs) == [4]


def test_safe_name():
    assert safe_name('a/b:c?.mp4') == "a_b_c_.mp4"
    long = "长" * 100 + ".mkv"
    out = safe_name(long)
    assert out.endswith(".mkv") and len(out.encode()) <= 120


def test_date_range():
    from datetime import date
    from paperplane.config import _parse_when
    since = _parse_when(date(2024, 1, 1), end_of_day=False)
    until = _parse_when("2024-01-31", end_of_day=True)
    cfg = ChannelConfig(chat="x", since=since, until=until)
    ts = lambda s: int(_parse_when(s, end_of_day=False).timestamp())
    msgs = [msg(1, "", "video"), msg(2, "", "video"), msg(3, "", "video"), msg(4, "", "video")]
    msgs[0].date = ts("2023-12-31 23:59")
    msgs[1].date = ts("2024-01-01")              # since 当天 0 点，包含
    msgs[2].date = ts("2024-01-31 23:59")        # until 当天晚上，包含
    msgs[3].date = ts("2024-02-01")
    assert ids(cfg, msgs) == [2, 3]


def test_parse_when_errors():
    import pytest
    from paperplane.config import _parse_when
    with pytest.raises(SystemExit):
        _parse_when("2024/01/01", end_of_day=False)
    assert _parse_when("2024-01-01T18:00:00+08:00", end_of_day=False).hour == 18
