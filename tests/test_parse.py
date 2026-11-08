import pytest

from conftest import PACK
from promo_finder.parse import parse_time, fmt_time, parse_show, parse_episode_text


def test_time_roundtrip():
    assert parse_time("01:13.10") == 73.1
    assert parse_time("1:02:03.5") == 3723.5
    assert parse_time("45.2") == 45.2
    assert fmt_time(73.1) == "01:13.10"


def test_synthetic_show(synth):
    assert [e.number for e in synth.episodes] == [1, 2]
    e1, e2 = synth.episodes
    assert len(e1.beats) == 15 and len(e2.beats) == 15
    # upper-case headers in ep 2 still parse
    assert e2.scenes[0].heading.startswith("INT. MIRA") and e2.scenes[0].end == 38.0
    b = e1.beats[2]
    assert b.kind == "dialogue" and b.speaker == "MIRA" and b.cues == "Warm, Excited"
    assert b.text == "Everything is perfect tonight." and b.id == "E01-B003"
    assert {x.kind for x in e2.beats} == {"camera", "dialogue", "action", "sfx"}


def test_malformed_block_warns_not_crashes():
    ep = parse_episode_text("EPISODE NUMBER: Episode 7\n\n**ACTION:**\nno timestamps here\n\n**BOB (Sad):**\n"
                            "Start: (00:01.00) | End: (00:02.00)\n\"hi\"\n")
    assert ep.number == 7 and len(ep.beats) == 1 and ep.warnings


@pytest.mark.skipif(not PACK.exists(), reason="set PROMO_TEST_PACK to a show folder")
def test_full_input_pack():
    show = parse_show(PACK)
    assert len(show.episodes) == 50
    assert sum(len(e.beats) for e in show.episodes) == 3727
    assert not show.warnings
    assert all(b.start <= b.end for e in show.episodes for b in e.beats)
    assert show.episode(6).scenes[1].start == 33.7
