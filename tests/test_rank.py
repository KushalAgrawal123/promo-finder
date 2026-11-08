from promo_finder.steps.rank import rank
from promo_finder.steps.validate import Promo, SegRef


def mk(pid, ep, arc, judge, cliff, hook_ok=True):
    p = Promo(id=pid, source="episode", title=pid, seg_refs=[SegRef("x", "y")], hook_beat="h", cliffhanger_beat=cliff,
              hook="", cliffhanger="", why_it_works="", tropes=["betrayal"],
              self_scores={"hook": 5, "cliffhanger": 5, "standalone": 5, "intensity": 5})
    p.segments = [{"episode": ep, "start": 0, "end": 30, "duration": 30}]
    p.total_duration = 30
    p.arc = arc
    p.checks = {"hook_in_3s": hook_ok}
    p.judge = {"hook_3s": judge, "cliffhanger": judge, "standalone": judge, "intensity": judge, "confusions": [],
               "would_keep_watching": True, "verdict": ""}
    return p


def test_score_and_penalties(cfg):
    a = mk("a", 20, "x", 8, "c1")
    b = mk("b", 20, "x", 8, "c2", hook_ok=False)
    c = mk("c", 3, "y", 8, "c3")
    out = rank([a, b, c], cfg["ranking"])
    assert out[0].id == "c"                      # funnel bonus for early episode
    assert a.score - b.score == cfg["ranking"]["penalty_no_hook_in_3s"]


def test_post_first_diversity(cfg):
    ps = [mk(f"p{i}", 20, "same-arc", 9 - i * 0.1, f"c{i}") for i in range(5)] + [mk("other", 30, "other-arc", 5, "z")]
    out = rank(ps, cfg["ranking"])
    chosen = [p for p in out if p.post_first]
    assert sum(1 for p in chosen if p.arc == "same-arc") == cfg["ranking"]["max_per_arc_in_post_first"]
    assert any(p.id == "other" for p in chosen)
    # shared cliffhanger beat is never posted twice
    d1, d2 = mk("d1", 20, "a1", 9, "same"), mk("d2", 25, "a2", 9, "same")
    assert sum(p.post_first for p in rank([d1, d2], cfg["ranking"])) == 1
