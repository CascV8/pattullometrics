"""Pattullometrics: the website over nhl-microstats, team-metrics and the NHLe cards. Player cards, the skater,
goalie and team pages, and the updater that rebuilds them as games come in."""

import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="pattullometrics", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    cd = sub.add_parser("cards", help="HTML player cards (default: top 12 F + top 4 D by points)")
    cd.add_argument("players", nargs="*", help="names (or parts) or player ids")
    cd.add_argument("--season", default="2025")
    cd.add_argument("--out", default="cards.html")
    cd.add_argument("--vs-season", default="last3", help="comparison pool: 'last3' (default), 'all', or a start year")

    st = sub.add_parser("site", help="export the static website to site/ (index + a card per player)")
    st.add_argument("seasons", nargs="*", help="start years (default: the season in progress and the last complete one)")
    st.add_argument("--out", default=None)

    for name, helptext in (("update", "one pass: pull newly official games, rebuild the season, re-export the site"),
                           ("watch", "run update every few minutes, forever")):
        u = sub.add_parser(name, help=helptext)
        u.add_argument("--season", default=None, help="start year (default: the season under way)")
        u.add_argument("--out", default=None)
        u.add_argument("--deploy", default=None, help="shell command to run after the site is rebuilt")
        u.add_argument("--force", action="store_true", help="rebuild and export even with nothing new")
        if name == "watch":
            u.add_argument("--every", type=float, default=5, help="minutes between passes (default 5)")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s  %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("puckscrape").setLevel(logging.WARNING)

    if args.cmd == "cards":
        from .cards import write_cards
        write_cards(args.players, season=args.season, out=args.out, vs_season=args.vs_season)
    elif args.cmd == "site":
        from .site import OUT, export_site
        export_site(args.seasons or None, args.out or OUT)
    elif args.cmd in ("update", "watch"):
        from .site import OUT
        from .update import update_once, watch
        kw = {"season": args.season, "out": args.out or OUT, "deploy": args.deploy, "force": args.force}
        if args.cmd == "watch":
            watch(args.every, **kw)
        elif update_once(**kw) and os.environ.get("GITHUB_OUTPUT"):   # tell the workflow to deploy
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("changed=true\n")
