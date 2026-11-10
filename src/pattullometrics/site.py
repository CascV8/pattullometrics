"""Static website: an index of every skater with category percentiles, and a player page
per skater that reuses the card. No server; any static host can serve ``site/``.

    nhl-microstats site                 # 2023-24 on (the curves' pool) -> site/
    nhl-microstats site 2026 2025 --out public

Layout of the output:

    index.html                  home page: a choice between the microstats and the NHLe prospect cards
    microstats.html             player directory and leaderboards
    player.html                 the card, loaded from ?season=YYYY&id=NNN
    goalies.html                every goalie in a season, sortable
    goalie.html                 a goalie's card, loaded from ?season=YYYY&id=NNN
    teams.html                  all 32 teams; team.html is one team's page, loaded from ?team=ABBR (teams.py)
    data/meta.json             seasons, when it was built, games included, games awaiting shift charts
    data/player_seasons.json    {player id: [seasons with a card]}, for the player page's season picker
    data/<season>/index.json    one compact row per skater
    data/<season>/players/<id>.json   one card's data (the same shape the cards page embeds)
    data/<season>/goalies.json  every goalie's ratings (goalies.py)
    data/teams/<season>/, logos/  the team pages' data (teams.py); data/teams/seasons.json lists the seasons

Everything is written to a temporary folder first and swapped in at the end, so a
visitor never sees half an update. Complete seasons don't change between games, so their
files are cached in data/site_cache/ and reused until the code or that season's data changes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import shutil
from pathlib import Path

import pandas as pd
import puckscrape as ps
from puckscrape import _http
import pyarrow.parquet as pq
from puckscrape._util import start_year

import nhl_microstats.cards
import nhl_microstats.curves
import nhl_microstats.db
import nhl_microstats.goalies
import nhl_microstats.percentile
import team_metrics.teams
from nhl_microstats import collect, goalies
from nhl_microstats.cards import MODES, card_data
from nhl_microstats.db import connect
from nhl_microstats.percentile import complete_seasons, season_name

from . import team_pages
from .cards import TEMPLATE

log = logging.getLogger("pattullometrics.site")

INDEX_TEMPLATE = Path(__file__).with_name("site_index.html")
HOME_TEMPLATE = Path(__file__).with_name("site_home.html")
# published name: template, for the goalie pages
GOALIE_PAGES = {"goalies.html": "site_goalies.html", "goalie.html": "site_goalie.html"}
TEAM_PAGES = {"teams.html": "site_teams.html", "team.html": "site_team.html"}
OUT = Path.cwd() / "site"
CACHE = collect.DATA / "site_cache"
# What a season's exported files depend on, besides its own rows: the code (here and in nhl-microstats) and the curves.
CODE = [Path(m.__file__) for m in (nhl_microstats.cards, nhl_microstats.curves, nhl_microstats.percentile,
                                    nhl_microstats.db, nhl_microstats.goalies)] + [Path(__file__)]
TEAM_CODE = [Path(team_metrics.teams.__file__), Path(__file__).with_name("team_pages.py")]


FIRST_SEASON = 2023   # the site starts with the first season of the curves' comparison pool (2023-24)


def default_seasons() -> list[int]:
    """Every season in the database from FIRST_SEASON on, newest first."""
    with connect() as con:
        return [r[0] for r in con.execute("SELECT DISTINCT season FROM player_seasons WHERE season >= ? "
                                          "ORDER BY season DESC", [FIRST_SEASON])]


def code_hash() -> str:
    """Changes whenever the code or templates that shape the site do."""
    h = hashlib.sha256()
    for path in [*CODE, *TEAM_CODE, TEMPLATE, INDEX_TEMPLATE, HOME_TEMPLATE,
                 *(Path(__file__).with_name(n) for n in [*GOALIE_PAGES.values(), *TEAM_PAGES.values()])]:
        h.update(path.read_bytes())
    return h.hexdigest()


def _stamp(y: int) -> str:
    """Changes whenever season ``y``'s exported files could: its data, the curves, or the code."""
    h = hashlib.sha256()
    for path in CODE:
        h.update(path.read_bytes())
    with connect() as con:
        for q, params in (("SELECT count(*), total(value) FROM stats WHERE season = ?", [y]),
                          ("SELECT count(*), total(toi_all), group_concat(team) FROM player_seasons WHERE season = ?", [y]),
                          ("SELECT count(*), total(a), total(b), total(c) FROM curves", [])):
            h.update(repr(con.execute(q, params).fetchone()).encode())
    return h.hexdigest()


def _export_season(y: int, folder: Path) -> dict:
    """Write one season's index and player files into ``folder``; returns its meta entry."""
    d = card_data(None, y)
    cards = d.pop("cards")
    (folder / "players").mkdir(parents=True)
    for c in cards:
        (folder / "players" / f"{c['id']}.json").write_text(json.dumps(d | {"site": True, "cards": [c]}), encoding="utf-8")
    bios = _bios(y)
    (folder / "index.json").write_text(json.dumps(d | {"rows": [_index_row(c, bios.get(c["id"])) for c in cards]}), encoding="utf-8")
    n_goalies = _export_goalies(y, not d["early"], folder)
    return {"season": y, "name": season_name([y]), "early": d["early"], "players": len(cards), "goalies": n_goalies}


def _export_goalies(y: int, complete: bool, folder: Path) -> int:
    """The season's goalie ratings (goalies.py) for the goalie pages; 0 if the NHL's report can't be had."""
    try:
        df = pd.read_parquet(collect.collect_goalies(y))
    except Exception as e:   # goalie cards are an extra: a site without them beats no site
        log.warning("%s: no goalie stats (%s)", y, e)
        return 0
    rows = goalies.goalie_ratings(df, complete, goalies.load_xg(y))
    bios = _bios(y, goalie=True)
    rows = [r | bios.get(r["id"], {}) for r in rows]
    (folder / "goalies.json").write_text(json.dumps({"season": y, "name": season_name([y]), "early": not complete,
                                                     **goalies.season_meta(df, complete), "goalies": rows}), encoding="utf-8")
    return len(rows)


def games_status(y: int) -> dict:
    """Games cached for a season, and which of them still have no shift charts. Past seasons'
    games aren't kept where the site is built (CI), so their count is left out there."""
    folder = collect.game_dir(y)
    if not folder.exists():
        return {"games": None, "awaiting_shifts": []}
    shifts = sorted(folder.glob("*_shifts.parquet"))
    missing = [int(p.stem.split("_")[0]) for p in shifts if pq.read_metadata(p).num_rows == 0]
    return {"games": len(list(folder.glob("*_pbp.parquet"))), "awaiting_shifts": missing}


ROOKIES = "https://api.nhle.com/stats/rest/en/{who}/bios"


def _rookies(y: int, goalie: bool) -> set[int]:
    """NHL ids of the season's rookies by the NHL's own rule (the Calder's: no more than 25 games in
    any earlier season, nor 6+ in each of two, and not 26 by September 15)."""
    d = _http.get_json(ROOKIES.format(who="goalie" if goalie else "skater"), params={
        "limit": -1, "cayenneExp": f"seasonId={y}{y + 1} and gameTypeId=2 and isRookie='1'"})
    return {int(r["playerId"]) for r in d.get("data", [])}


def _bios(y: int, goalie: bool = False) -> dict:
    """Birth date, nationality, shooting side, draft (year, round, overall) and whether he's a rookie,
    by NHL id, from the NHL's bios report for the season; {} if the report can't be had."""
    try:
        df = (ps.nhl.goalie_stats if goalie else ps.nhl.skater_stats)(y, report="bios")
        rookies = _rookies(y, goalie)
    except Exception as e:   # the directory's filters are an extra
        log.warning("%s: no bios (%s)", y, e)
        return {}
    num = lambda v: int(v) if isinstance(v, (int, float)) and v == v else None
    text = lambda v: v if isinstance(v, str) else ""   # a blank comes through as NaN, which isn't JSON
    return {int(r["player_id"]): {
        "born": text(r.get("birth_date")) or None, "nat": text(r.get("nationality_code")) or text(r.get("birth_country_code")),
        "shoots": text(r.get("shoots_catches")),
        "draft": [num(r.get("draft_year")), num(r.get("draft_round")), num(r.get("draft_overall"))] if num(r.get("draft_round")) else None,
        "rookie": int(r["player_id"]) in rookies}
        for r in df.to_dict("records") if r.get("player_id") == r.get("player_id")}


def _index_row(c: dict, bio: dict | None = None) -> dict:
    """The few numbers the directory shows, per mode, and his bio for its filters."""
    modes = {}
    for mode in MODES:
        o = (c.get("overall") or {}).get(mode)
        modes[mode] = {"overall": [o[2], o[0]] if o else None,
                       "cats": {title: [pct, avg] for title, avg, _, pct in c["categories"][mode]}}
    return {"id": c["id"], "name": c["name"], "team": c["team"], "pos": c["position"], "group": c["group"],
            "gp": c["gp"], "toi": c["toi"], "g": c["g"], "a": c["a"], "modes": modes, **(bio or {})}


def export_site(seasons=None, out: Path | str = OUT) -> Path:
    out = Path(out)
    seasons = [start_year(s) for s in seasons] if seasons else default_seasons()
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "data").mkdir(parents=True)

    meta = {"built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "seasons": []}
    done = set(complete_seasons())
    for y in sorted(seasons, reverse=True):
        folder = tmp / "data" / str(y)
        cached = CACHE / str(y)
        if y in done and (cached / "stamp").exists() and (cached / "stamp").read_text() == _stamp(y):
            shutil.copytree(cached / "files", folder)
            entry = json.loads((cached / "meta.json").read_text())
            log.info("%s: reused (unchanged)", entry["name"])
        else:
            entry = _export_season(y, folder)
            log.info("%s: %d players", entry["name"], entry["players"])
            if y in done:     # keep it for next time
                if cached.exists():
                    shutil.rmtree(cached)
                shutil.copytree(folder, cached / "files")
                (cached / "meta.json").write_text(json.dumps(entry))
                (cached / "stamp").write_text(_stamp(y))
        meta["seasons"].append(entry | games_status(y))
    (tmp / "data" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with connect() as con:
        rows = con.execute(f"SELECT player_id, season FROM player_seasons WHERE season IN ({','.join('?' * len(seasons))}) "
                           "ORDER BY season DESC", seasons).fetchall()
    by_player = {}
    for pid, y in rows:
        by_player.setdefault(str(pid), []).append(y)
    (tmp / "data" / "player_seasons.json").write_text(json.dumps(by_player), encoding="utf-8")
    shutil.copy(HOME_TEMPLATE, tmp / "index.html")
    shutil.copy(INDEX_TEMPLATE, tmp / "microstats.html")
    for name, src in {**GOALIE_PAGES, **TEAM_PAGES}.items():
        shutil.copy(Path(__file__).with_name(src), tmp / name)
    # team pages are an extra, like the goalie cards: a site without them beats no site
    # every season's skaters, the pool the teams' roster ratings are curved against; and each team's analytical and
    # standings ranks by season (data/teams/history.json, the team page's chart)
    team_seasons, history = [], {}
    pool = {y: json.loads((tmp / "data" / str(y) / "index.json").read_text(encoding="utf-8"))["rows"]
            for y in seasons if (tmp / "data" / str(y) / "index.json").exists()}
    for y in sorted(seasons, reverse=True):
        try:
            if ranked := team_pages.export_teams(tmp / "data" / str(y), y, tmp, pool):
                team_seasons.append({"season": y, "name": season_name([y])})
                for abbr, r in ranked.items():
                    history.setdefault(abbr, []).append({"season": y, "name": season_name([y])} | r)
        except Exception as e:
            log.warning("%s: no team pages (%s)", y, e)
    if team_seasons:
        (tmp / "data" / "teams" / "seasons.json").write_text(json.dumps(team_seasons), encoding="utf-8")
        for h in history.values():
            h.sort(key=lambda e: e["season"])
        (tmp / "data" / "teams" / "history.json").write_text(json.dumps(history), encoding="utf-8")
    # The card template is a fragment (cards.html gets wrapped by whatever shows it); give it a document head.
    head = '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    (tmp / "player.html").write_text(head + TEMPLATE.read_text(encoding="utf-8").replace(
        "<title>NHL Microstat Cards</title>", "<title>Player card · NHL Microstats</title>", 1), encoding="utf-8")

    old = out.with_name(out.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    if old.exists():
        shutil.rmtree(old)
    log.info("site written to %s", out.resolve())
    return out
