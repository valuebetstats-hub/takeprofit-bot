"""python -m pytest test_takeprofit_bot.py -- checks the selection and stop rules, no Betfair account needed."""
import json, os, types
import pytest
import takeprofit_bot as tb

def R(sid, back=None, near=None, status="ACTIVE"):
    return types.SimpleNamespace(selection_id=sid, status=status, handicap=0,
                                 sp=types.SimpleNamespace(near_price=near),
                                 ex=types.SimpleNamespace(available_to_back=[{"price": back, "size": 50}] if back else []))

def test_all_takes_every_runner_in_band():
    rs = [R(1, 2.2), R(2, 2.6), R(3, 2.98), R(4, 3.0), R(5, 7.0)]
    assert [r.selection_id for r in tb.select_runners(rs, "all", 2.5, 3.0)] == [2, 3]   # 3.0 itself is out

def test_fav_only_the_shortest_and_only_in_band():
    assert [r.selection_id for r in tb.select_runners([R(1, 2.7), R(2, 3.5)], "fav", 2.5, 3.0)] == [1]
    assert tb.select_runners([R(1, 2.2), R(2, 2.7)], "fav", 2.5, 3.0) == []             # fav 2.2: no bet, even though 2.7 is in band
    assert tb.select_runners([R(1, 2.7), R(2, 2.7)], "fav", 2.5, 3.0) == []             # joint favourites: skip

def test_projected_sp_beats_back_price_and_non_runners_ignored():
    assert tb.price_of(R(1, back=2.4, near=2.62)) == 2.62
    rs = [R(1, 2.7, status="REMOVED"), R(2, 2.9)]
    assert [r.selection_id for r in tb.select_runners(rs, "fav", 2.5, 3.0)] == [2]

def test_points():
    assert tb.bet_pnl(True, 2.8, 0.02) == pytest.approx(1.764)
    assert tb.bet_pnl(False, 2.8, 0.02) == -1.0

def test_take_profit_and_stop_loss(tmp_path):
    lv = {"Southwell": {"take_profit": 10, "stop_loss": 10.5}, "Yarmouth": {"take_profit": 10, "stop_loss": 8.2}}
    st = tb.State(str(tmp_path / "s.json"), lv)
    for _ in range(5):
        st.add("Southwell", 2027, 1.9)
    assert st.is_on("Southwell", 2027)
    assert st.add("Southwell", 2027, 1.9) == "TAKE-PROFIT" and not st.is_on("Southwell", 2027)   # 11.4 >= 10
    for _ in range(9):
        st.add("Yarmouth", 2027, -1.0)
    assert not st.is_on("Yarmouth", 2027)                                                         # -9 <= -8.2
    assert st.is_on("Southwell", 2028) and st.is_on("Yarmouth", 2028)                            # new year, fresh start
    assert not tb.State(str(tmp_path / "s.json"), lv).is_on("Southwell", 2027)                   # survives a restart

def test_config_both_strategies():
    here = os.path.dirname(os.path.abspath(__file__))
    f = tb.load_config(os.path.join(here, "config.json"), "fav")
    a = tb.load_config(os.path.join(here, "config.json"), "all")
    assert set(f["levels"]) == {"Southwell", "Yarmouth"}
    assert set(a["levels"]) == {"Southwell", "Newcastle", "Dundalk", "Yarmouth"}
    assert a["aliases"]["great yarmouth"] == "Yarmouth"
