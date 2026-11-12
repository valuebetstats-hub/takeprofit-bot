# EdgeFinder take-profit bot

From **@valuebetstats** (EdgeFinder). A small Betfair bot that backs horses at **Betfair SP
between 2.5 and 3.0** at a short list of courses. Once a course is **+10 points up** for the
year it stops betting that course (take-profit). If it falls to that course's **stop-loss**
it also stops. Everything starts again on 1 January.

> Research tool, not betting advice. Past results don't predict future ones.
> Only bet what you can afford to lose. 18+ · BeGambleAware.org

---

## 1. Pick your version (you must choose one)

| `--strategy` | What it backs | Courses and levels (points) |
|---|---|---|
| `fav` | The race **favourite**, only when it is priced 2.5–3.0 | Southwell: +10 / −11.3 · Yarmouth: +10 / −7.2 |
| `all` | **Every horse** priced 2.5–3.0, favourite or not | Southwell: +10 / −10.5 · Newcastle: +10 / −10.9 · Dundalk: +10 / −8.5 · Yarmouth: +10 / −8.2 |

`all` places more bets: about 18% of the 2.5–3.0 horses aren't the favourite. Run one version
at a time. Each keeps its own results file, so you can try both on paper on different days.

## 2. What you need

* A **Betfair account** with API access and your own **live application key**
  (https://developer.betfair.com → "Getting started"). The delayed key won't work.
* **Python 3.10 or newer**.

## 3. Install

Open a terminal in this folder:

```
pip install -r requirements.txt
```

## 4. Add YOUR Betfair details

1. Copy `betfair.env.example` to a new file called `betfair.env`.
2. Fill in your own username, password and live app key.
3. Leave `BF_CERT_DIR` out unless you've set up a Betfair API certificate. Without it the bot
   uses the simpler interactive login.

`betfair.env` holds your password: keep it on your own computer and never share or post it.
The bot only reads it to log in. It never prints or saves your details anywhere else.

## 5. Run it on PAPER first (the default)

```
python takeprofit_bot.py --strategy all
```
or
```
python takeprofit_bot.py --strategy fav
```

Start it each racing day before the first race; it shuts itself down at 21:30. On paper it
places **no bets**. About 30 seconds before each race at a course on the list, it checks the
prices. It logs every horse it would back, and after the race settles that bet at the real
Betfair SP.

See where each course stands this year:

```
python takeprofit_bot.py --strategy all --status
```

Files it writes in this folder:

| File | What's in it |
|---|---|
| `bets_all.csv` / `bets_fav.csv` | Every bet: horse, price at entry, Betfair SP, won/lost, points, course total |
| `state_all.json` / `state_fav.json` | Each course's points this year and whether it's ON, TAKE-PROFIT or STOP-LOSS |
| `takeprofit_all.log` / `takeprofit_fav.log` | What the bot did, minute by minute |

## 6. Going LIVE (real money)

Only after you've watched it on paper:

```
python takeprofit_bot.py --strategy all --live --confirm-live --stake 2
```

* `--stake` is the money per point (default 1.0, set in `config.json`). A €2 stake makes
  +10 points = +€20.
* Live bets are placed as **Betfair SP back bets** (Market On Close), so you get the
  Betfair SP like the research.
* Betfair has a minimum stake for SP bets. If yours is below it, Betfair rejects the bet and
  the log says so: check Betfair's current minimum for your currency.
* Each bet is capped at your stake. The bot never lays and never bets in-play.

## 7. How it decides

1. It watches GB & IRE win markets at the courses for your version.
2. About 30 seconds before the off it reads each horse's price: Betfair's projected SP if
   available, otherwise the best back price.
   * `all`: backs every horse priced 2.5 up to (not including) 3.0.
   * `fav`: backs only the shortest-priced horse, and only if it's 2.5–3.0. A joint favourite
     is skipped.
3. The bet settles at Betfair SP. 1 point won = (SP − 1) × 0.98 (2% commission); a loser = −1.
4. After each result it adds the points to that course's total for the year:
   * total **≥ +take-profit** → course OFF for the rest of the year (profit banked)
   * total **≤ −stop-loss** → course OFF for the rest of the year (loss capped)
   * otherwise it keeps going.

The price 30 seconds before the off isn't always the final SP. A horse can be picked at 2.9 and
go off at 3.1, or the reverse. The research used the final Betfair SP, so expect small
differences.

## 8. Where the levels came from

1. Every GB & IRE race 2024–2026, Betfair SP, 1 point a bet, 2% commission.
2. For each course: its running points through the year, and its highest point in each year.
3. Kept only the courses that got above +10 points in at least 2 of the 3 years.
4. **Take-profit = +10 points.**
5. **Stop-loss** = the deepest dip on the way to +10 in those years, minus 5 points.

Those levels were set from the same years they were checked on, so the bot is now being run
forward to see if they hold. The 2.5–3.0 band as a whole is roughly break-even at Betfair SP.
The take-profit and stop-loss change how often you win and how big the wins are. They don't
create an edge on their own.

## 9. Changing things

Everything is in `config.json`: stake, commission, the price band, the courses and their
take-profit / stop-loss (in points). Add a course by copying a line and using the course name
as Betfair shows it. Use `betfair_names` if Betfair spells it differently.

## 10. Check the logic

```
python -m pytest test_takeprofit_bot.py
```

This checks the price band, the favourite rule, the points maths and the take-profit / stop-loss
rules, with no Betfair account needed.
