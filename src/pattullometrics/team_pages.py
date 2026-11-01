"""Team pages for the site: each team's page file and the index, from team-metrics' season_teams(), and the
teams' logos (served with the site so the page can read and crop them; the NHL's asset server allows no
cross-origin reads).

    data/teams/<year>/index.json    the teams: abbrev, name, record, overall rank
    data/teams/<year>/<ABBR>.json   one team's page (team.html?team=ABBR&season=YYYY)
    logos/<ABBR>.svg                the team's logo
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from nhl_microstats import collect
from nhl_microstats.percentile import season_name
from team_metrics.teams import TEAM_STATS, _get, _group, season_teams

log = logging.getLogger("pattullometrics.team_pages")

LOGOS = collect.DATA / "logos"              # <ABBR>.svg: the NHL's dark-background logos, refreshed every LOGO_DAYS
LOGO_DAYS = 30


def _logo(abbr: str, out: Path) -> None:
    """Copy the team's logo into the site, fetching it when ours is missing or older than LOGO_DAYS."""
    LOGOS.mkdir(parents=True, exist_ok=True)
    path = LOGOS / f"{abbr}.svg"
    age = dt.datetime.now().timestamp() - path.stat().st_mtime if path.exists() else None
    if age is None or age > LOGO_DAYS * 86400:
        try:
            path.write_bytes(_get(f"https://assets.nhle.com/logos/nhl/svg/{abbr}_dark.svg").content)
        except Exception as e:
            log.warning("%s: logo not fetched (%s)", abbr, e)
    if path.exists():
        (out / "logos").mkdir(exist_ok=True)
        (out / "logos" / path.name).write_bytes(path.read_bytes())


# ── Matching Daily Faceoff's names to NHL ids ────────────────────────────────────────────────────────────


def export_teams(season_dir: Path, y: int, out: Path) -> int:
    """Write data/teams/<y>/ into ``out`` from season ``y``'s exported skaters and goalies (``season_dir``): the season
    under way from Daily Faceoff and today's standings, a complete season from its lineup after the trade deadline
    (team_seasons/<y>.json). Returns the number of team pages written."""
    rows = json.loads((season_dir / "index.json").read_text(encoding="utf-8"))["rows"]
    gpath = season_dir / "goalies.json"
    gdata = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else {"goalies": []}
    skaters = {r["id"]: r for r in rows}
    goalies = {g["id"]: g for g in gdata["goalies"]}
    st = season_teams(y, rows, gdata)
    if st is None:
        return 0
    teams, values, ranks, overall, metrics = st["teams"], st["values"], st["ranks"], st["overall"], st["metrics"]


    folder = out / "data" / "teams" / str(y)
    folder.mkdir(parents=True, exist_ok=True)
    note = (f"Overall: the average of GSAx/GP, SV% and GA/GP percentiles, curved against the {gdata.get('pool', 0)} goalies "
            f"with {gdata.get('min_gp', 0)}+ games.") if gdata["goalies"] else ""
    index = []
    for t in sorted(teams, key=lambda t: t["name"]):
        abbr, name = t["abbrev"], t["name"]

        def card(pid, name, slot):
            r, g = skaters.get(pid), goalies.get(pid)
            c = {"id": pid, "name": name, "slot": slot}
            if g and _group(slot) == "G" or g and not r:
                m = {x["key"]: x for x in g["metrics"]}
                sv = m.get("sv", {}).get("value")
                return c | {"pos": "G", "ovr": g.get("overall"), "gp": g["gp"], "rec": f'{g["w"]}-{g["l"]}-{g["otl"]}',
                            "sv": f"{sv:.3f}".lstrip("0") if sv is not None else ""}
            if r:
                o = r["modes"]["5v5"]["overall"]
                return c | {"pos": r["pos"], "ovr": o[0] if o else None, "gp": r["gp"], "g": r["g"], "a": r["a"],
                            "pts": r["g"] + r["a"]}
            return c | {"pos": "G" if _group(slot) == "G" else slot, "ovr": None}

        def lineup(lu: dict) -> dict:
            """One version of the lineup (latest, post-deadline, opening night) as the page draws it. Extras are None
            where the version has none to show (opening night: only the dressed players are known)."""
            groups = lu["groups"]
            cards = lambda key: [card(*p) for p in groups.get(key, [])]
            return {"key": lu["key"], "label": lu["label"], "source": lu["source"],
                    "lines": [cards(f"f{i}") for i in range(1, 5) if groups.get(f"f{i}")],
                    "pairs": [cards(f"d{i}") for i in range(1, 4) if groups.get(f"d{i}")], "goalies": cards("g"),
                    "pp": [cards("pp1"), cards("pp2")], "pk": [cards("pk1"), cards("pk2")], "injured": cards("ir"),
                    "extras": None if lu["extras"] is None else [card(*p) for p in lu["extras"]]}

        # the metrics in order, each with its rank; P%, PP% and PK% with the stat itself
        shown = [{"k": m, "rank": ranks[m][abbr],
                  "value": f"{100 * values[abbr][m]:.1f}%" if m in dict(TEAM_STATS).values() and values[abbr][m] is not None else ""}
                 for m in metrics]
        page = {
            "abbrev": abbr, "name": name, "season": y, "seasonName": season_name([y]),
            "record": t["record"], "points": t["points"], "gp": t["gp"],
            "rank": overall[abbr], "teams": len(values), "metrics": shown,
            "lineups": [lineup(lu) for lu in t["lineups"]], "goalieNote": note,
        }
        (folder / f"{abbr}.json").write_text(json.dumps(page), encoding="utf-8")
        _logo(abbr, out)
        index.append({"abbrev": abbr, "name": name, "record": page["record"], "points": page["points"], "rank": page["rank"]})
    index.sort(key=lambda t: (t["rank"] or len(index) + 1, t["name"]))   # best first
    (folder / "index.json").write_text(json.dumps({"season": y, "seasonName": season_name([y]), "teams": index}), encoding="utf-8")
    log.info("teams: %d team pages", len(index))
    return len(index)
