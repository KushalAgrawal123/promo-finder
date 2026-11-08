from promo_finder.steps.validate import Promo, SegRef, validate_promo, dedupe, iou


def mk(refs, hook, cliff, pid="T1"):
    return Promo(id=pid, source="episode", title="t", seg_refs=[SegRef(a, b) for a, b in refs], hook_beat=hook,
                 cliffhanger_beat=cliff, hook="", cliffhanger="", why_it_works="", tropes=[],
                 self_scores={"hook": 5, "cliffhanger": 5, "standalone": 5, "intensity": 5})


def test_valid_single_segment(synth, cfg):
    # B005 Tara "Not for you" (9.2s) .. B013 Mira "this isn't his signature" (ends 36.0)
    p = mk([("E01-B005", "E01-B014")], "E01-B005", "E01-B014")
    assert validate_promo(p, synth, cfg) == []
    s = p.segments[0]
    assert s["start"] == 9.2 and s["end"] == 36.0
    assert p.checks["hook_in_3s"] and 20 <= p.total_duration <= 60


def test_hook_rule_tightens_start(synth, cfg):
    # starts on the establishing shot (0.0) but the hook (Tara's line) is at 9.2s -> start tightened
    p = mk([("E01-B001", "E01-B014")], "E01-B005", "E01-B014")
    assert validate_promo(p, synth, cfg) == []
    assert abs(p.segments[0]["start"] - 8.9) < 1e-6
    assert p.checks["trimmed_start_s"] > 8 and p.checks["hook_in_3s"]


def test_cliffhanger_rule_cuts_after_cliff(synth, cfg):
    # range runs to the end of the episode, but the cliffhanger is Mira's line (ends 36.0):
    # the trailing camera shot may extend by at most 2s
    p = mk([("E01-B005", "E01-B015")], "E01-B005", "E01-B014")
    assert validate_promo(p, synth, cfg) == []
    assert p.segments[0]["end"] == 36.0
    assert p.checks["trimmed_end_s"] == 4.0


def test_duration_too_short(synth, cfg):
    p = mk([("E01-B005", "E01-B008")], "E01-B005", "E01-B008")
    errs = validate_promo(p, synth, cfg)
    assert any("< 20" in e for e in errs)


def test_unknown_beat_and_cross_episode(synth, cfg):
    assert any("unknown beat" in e for e in validate_promo(mk([("E01-B005", "E01-B999")], "E01-B005", "E01-B999"), synth, cfg))
    assert any("two episodes" in e for e in validate_promo(mk([("E01-B005", "E02-B003")], "E01-B005", "E02-B003"), synth, cfg))


def test_multi_segment_across_episodes(synth, cfg):
    p = mk([("E01-B005", "E01-B009"), ("E02-B007", "E02-B014")], "E01-B005", "E02-B014")
    assert validate_promo(p, synth, cfg) == []
    assert [s["episode"] for s in p.segments] == [1, 2]
    assert abs(p.total_duration - sum(s["duration"] for s in p.segments)) < 1e-6


def test_invalid_hook_id_falls_back(synth, cfg):
    p = mk([("E01-B005", "E01-B014")], "E01-B777", "E01-B014")
    assert validate_promo(p, synth, cfg) == []
    assert p.checks.get("hook_beat_fallback") and p.hook_beat == "E01-B005"


def test_dedupe(synth, cfg):
    a = mk([("E01-B005", "E01-B014")], "E01-B005", "E01-B014", "A")
    b = mk([("E01-B006", "E01-B014")], "E01-B006", "E01-B014", "B")
    for p in (a, b):
        assert validate_promo(p, synth, cfg) == []
    a.self_scores["hook"] = 9
    assert iou(a, b) > 0.5
    kept, dropped = dedupe([a, b], 0.5, key=lambda p: p.self_avg())
    assert [p.id for p in kept] == ["A"] and dropped[0][0].id == "B"
