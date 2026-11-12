#!/usr/bin/env python3
"""
takeprofit_bot.py -- the EdgeFinder "take-profit bot" (@valuebetstats).

Backs horses at Betfair SP in the 2.5-3.0 price band at a short list of courses, and stops
betting a course for the rest of the calendar year once that course is +X points up
(take-profit) or -Y points down (stop-loss). You choose ONE of two versions:

    --strategy fav   back the race FAVOURITE, only when it's priced 2.5-3.0
    --strategy all   back EVERY horse priced 2.5-3.0 (favourite or not)

Each version has its own course list and its own take-profit / stop-loss levels
(config.json). PAPER mode is the default: nothing is bet, every would-be bet is logged and
settled at the real Betfair SP so you can follow it. LIVE mode needs --live --confirm-live.

Read README.md first. This is a research tool, not betting advice. 18+ BeGambleAware.org
"""
from __future__ import annotations
import argparse, csv, datetime as dt, json, logging, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("takeprofit")


# ----------------------------------------------------------------------------- pure logic
# (no Betfair imports here, so these can be tested without an account)

def price_of(runner) -> float | None:
    """The price we judge the band on before the off: Betfair's projected SP (near price) when
    it's there, else the best back price on offer."""
    sp = getattr(runner, "sp", None)
    near = getattr(sp, "near_price", None) if sp is not None else None
    if near and near not in ("NaN", "Infinity") and 1.0 < float(near) < 1000:
        return float(near)
    ex = getattr(runner, "ex", None)
    backs = getattr(ex, "available_to_back", None) or []
    if backs:
        b = backs[0]
        return float(b["price"] if isinstance(b, dict) else b.price)
    return None


def select_runners(runners, strategy: str, lo: float, hi: float) -> list:
    """The runners to back in this race. 'all': every ACTIVE runner priced lo <= price < hi.
    'fav': only the single shortest-priced ACTIVE runner, and only if it is in the band
    (a joint favourite -- two runners at the same shortest price -- is skipped)."""
    priced = [(price_of(r), r) for r in runners if getattr(r, "status", "ACTIVE") == "ACTIVE"]
    priced = [(p, r) for p, r in priced if p is not None]
    if not priced:
        return []
    if strategy == "all":
        return [r for p, r in priced if lo <= p < hi]
    priced.sort(key=lambda t: t[0])
    if len(priced) > 1 and priced[0][0] == priced[1][0]:
        return []
    p, r = priced[0]
    return [r] if lo <= p < hi else []


def bet_pnl(won: bool, bsp: float, commission: float) -> float:
    """Points won or lost by a 1-point back at Betfair SP."""
    return (bsp - 1.0) * (1.0 - commission) if won else -1.0


class State:
    """Running points per course for the current calendar year, saved to disk so the bot can
    be stopped and restarted. A course is OFF for the year once it hits its take-profit or
    stop-loss. Everything resets on 1 January."""

    def __init__(self, path: str, levels: dict):
        self.path, self.levels = path, levels
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)

    def _year(self, year: int) -> dict:
        y = self.data.setdefault(str(year), {})
        for c in self.levels:
            y.setdefault(c, {"points": 0.0, "bets": 0, "status": "ON"})
        return y

    def is_on(self, course: str, year: int) -> bool:
        return course in self.levels and self._year(year)[course]["status"] == "ON"

    def add(self, course: str, year: int, pts: float) -> str:
        c = self._year(year)[course]
        c["points"] = round(c["points"] + pts, 4); c["bets"] += 1
        lv = self.levels[course]
        if c["status"] == "ON":
            if c["points"] >= lv["take_profit"]:
                c["status"] = "TAKE-PROFIT"
            elif c["points"] <= -lv["stop_loss"]:
                c["status"] = "STOP-LOSS"
        self.save()
        return c["status"]

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1)
        os.replace(tmp, self.path)

    def summary(self, year: int) -> str:
        y = self._year(year)
        return " | ".join(f"{c} {v['points']:+.2f} pts ({v['bets']} bets, {v['status']})" for c, v in y.items())


def load_config(path: str, strategy: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    s = cfg["strategies"][strategy]
    out = dict(cfg)
    out["levels"] = s["courses"]
    out["band"] = s.get("band", cfg.get("band", [2.5, 3.0]))
    aliases = {}
    for course, lv in s["courses"].items():
        for name in [course] + lv.get("betfair_names", []):
            aliases[name.lower()] = course
    out["aliases"] = aliases
    return out


def load_env(path: str) -> None:
    """KEY=VALUE lines from your own credentials file into the environment (never printed)."""
    if not path or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ----------------------------------------------------------------------------- the bot

def run(args) -> None:
    import betfairlightweight
    from betfairlightweight.filters import streaming_market_filter
    from flumine import BaseStrategy, Flumine, clients
    from flumine.order.ordertype import MarketOnCloseOrder
    from flumine.order.trade import Trade
    from flumine.streams.betfairmarketstream import BetfairMarketStream
    from flumine.worker import BackgroundWorker
    from flumine.events.events import CustomEvent, TerminationEvent

    cfg = load_config(args.config, args.strategy)
    lo, hi = cfg["band"]
    stake = float(args.stake if args.stake is not None else cfg.get("stake", 1.0))
    commission = float(cfg.get("commission", 0.02))
    state = State(os.path.join(HERE, f"state_{args.strategy}.json"), cfg["levels"])
    log_path = os.path.join(HERE, f"bets_{args.strategy}.csv")
    live = args.live
    year = dt.date.today().year
    log.info("strategy=%s  band %.2f-%.2f  stake %.2f a point  %s", args.strategy, lo, hi, stake,
             "LIVE -- real money" if live else "PAPER -- nothing is bet")
    log.info("courses this year: %s", state.summary(year))

    load_env(args.env_file)
    missing = [k for k in ("BF_USERNAME", "BF_PASSWORD", "BF_APP_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing {', '.join(missing)} -- put YOUR Betfair details in {args.env_file} "
                 "(copy betfair.env.example) -- see README.md")
    certs = os.environ.get("BF_CERT_DIR") or None
    trading = betfairlightweight.APIClient(username=os.environ["BF_USERNAME"], password=os.environ["BF_PASSWORD"],
                                           app_key=os.environ["BF_APP_KEY"], certs=certs)
    trading.login() if certs else trading.login_interactive()
    log.info("logged in (%s login)", "certificate" if certs else "interactive")

    def write_bet(row):
        new = not os.path.exists(log_path)
        with open(log_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["date", "off", "course", "market_id", "selection_id", "horse", "price_at_entry",
                            "mode", "bsp", "won", "points", "course_points", "course_status"])
            w.writerow(row)

    class TakeProfitBot(BaseStrategy):
        def start(self, flumine=None):
            self.done, self.bets, self.names = set(), {}, {}

        def check_market_book(self, market, market_book):
            if market_book.status != "OPEN" or market_book.inplay:
                return market_book.status == "CLOSED" and market.market_id in self.bets
            course = cfg["aliases"].get((market_book.market_definition.venue or "").lower())
            return bool(course) and state.is_on(course, year)

        def process_market_book(self, market, market_book):
            self.try_entry(market, market_book)

        def try_entry(self, market, market_book):
            mid = market.market_id
            if mid in self.done or market_book.status != "OPEN" or market_book.inplay:
                return
            sts = market.seconds_to_start
            if sts is None or sts > args.entry_seconds or sts < 5:
                return
            self.done.add(mid)
            course = cfg["aliases"].get((market_book.market_definition.venue or "").lower())
            if not course or not state.is_on(course, year):
                return
            picks = select_runners(market_book.runners, args.strategy, lo, hi)
            names = {r.selection_id: r.runner_name for r in (market.market_catalogue.runners if market.market_catalogue else [])}
            for r in picks:
                px = price_of(r); name = names.get(r.selection_id, str(r.selection_id))
                self.bets.setdefault(mid, []).append(dict(sid=r.selection_id, name=name, px=px, course=course))
                if live:
                    trade = Trade(market_id=mid, selection_id=r.selection_id, handicap=r.handicap, strategy=self)
                    order = trade.create_order(side="BACK", order_type=MarketOnCloseOrder(liability=stake))
                    market.place_order(order)
                log.info("%s %s  %s  %s  price %.2f -> %s 1 pt at Betfair SP", course, mid, name,
                         "BACK" if live else "paper back", px, "placed" if live else "logged")

        def process_closed_market(self, market, market_book):
            for b in self.bets.pop(market.market_id, []):
                r = next((x for x in market_book.runners if x.selection_id == b["sid"]), None)
                bsp = getattr(getattr(r, "sp", None), "actual_sp", None) if r else None
                if not bsp:                     # fall back to the closed market's definition
                    rd = next((x for x in (market_book.market_definition.runners or []) if x.selection_id == b["sid"]), None)
                    bsp = getattr(rd, "bsp", None)
                if r is None or r.status == "REMOVED" or not bsp or str(bsp) in ("NaN", "Infinity"):
                    log.info("%s %s: non-runner / no SP -- no bet", b["course"], b["name"]); continue
                won = r.status == "WINNER"; pts = bet_pnl(won, float(bsp), commission)
                status = state.add(b["course"], year, pts)
                c = state._year(year)[b["course"]]
                write_bet([dt.date.today().isoformat(), str(market.market_start_datetime)[11:16], b["course"],
                           market.market_id, b["sid"], b["name"], round(b["px"], 2), "LIVE" if live else "PAPER",
                           round(float(bsp), 2), int(won), round(pts, 2), round(c["points"], 2), status])
                log.info("RESULT %s %s BSP %.2f %s %+.2f pts | %s now %+.2f pts (%s)", b["course"], b["name"],
                         float(bsp), "WON" if won else "lost", pts, b["course"], c["points"], status)

    def sweep(fw, ev, strat):           # runs on flumine's main thread: catches quiet markets
        for m in list(fw.markets):
            if m.market_book is not None and strat.check_market_book(m, m.market_book):
                strat.try_entry(m, m.market_book)
        now = dt.datetime.now().strftime("%H:%M")
        if now >= args.stop_at and not any(strat.bets.values()):
            log.info("stop time %s reached -- %s", args.stop_at, state.summary(year))
            fw.handler_queue.put(TerminationEvent(None))

    client = clients.BetfairClient(trading, paper_trade=not live, interactive_login=certs is None)
    framework = Flumine(client=client)
    stream = BetfairMarketStream(
        market_filter=streaming_market_filter(event_type_ids=["7"], country_codes=["GB", "IE"], market_types=["WIN"]),
        market_data_filter={"fields": ["EX_BEST_OFFERS", "EX_MARKET_DEF", "SP_PROJECTED", "SP_TRADED"], "ladderLevels": 1})
    strat = TakeProfitBot(stream=stream, max_order_exposure=stake * 1.01, max_selection_exposure=stake * 1.01,
                          max_trade_count=200, max_live_trade_count=10)
    framework.add_strategy(strat)
    framework.add_worker(BackgroundWorker(framework, lambda context, flumine: flumine.handler_queue.put(
        CustomEvent({}, lambda fl, ev: sweep(fl, ev, strat))), interval=5, name="sweep"))
    framework.run()
    log.info("finished -- %s", state.summary(year))


def main():
    ap = argparse.ArgumentParser(description="EdgeFinder take-profit bot -- read README.md first")
    ap.add_argument("--strategy", required=True, choices=["fav", "all"],
                    help="fav = the favourite only, when 2.5-3.0 | all = every horse at 2.5-3.0")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--env-file", default=os.path.join(HERE, "betfair.env"),
                    help="YOUR Betfair details (copy betfair.env.example)")
    ap.add_argument("--stake", type=float, default=None, help="money per point (default: config.json)")
    ap.add_argument("--entry-seconds", type=float, default=30, help="bet this many seconds before the off")
    ap.add_argument("--stop-at", default="21:30", help="local time to shut down (HH:MM)")
    ap.add_argument("--status", action="store_true", help="print this year's course totals and exit")
    ap.add_argument("--live", action="store_true", help="REAL bets (default: paper)")
    ap.add_argument("--confirm-live", action="store_true", help="required with --live")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(os.path.join(HERE, f"takeprofit_{a.strategy}.log"), encoding="utf-8")])
    if a.status:
        cfg = load_config(a.config, a.strategy)
        print(State(os.path.join(HERE, f"state_{a.strategy}.json"), cfg["levels"]).summary(dt.date.today().year)); return
    if a.live and not a.confirm_live:
        sys.exit("--live bets real money: add --confirm-live as well")
    run(a)


if __name__ == "__main__":
    main()
