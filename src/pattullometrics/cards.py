"""Player cards as one self-contained HTML page: nhl-microstats' card numbers in cards_template.html.

    pattullometrics cards                      # top 12 forwards + top 4 D by points
    pattullometrics cards McDavid Makar Hutson --out my_cards.html
"""

from __future__ import annotations

import json
from pathlib import Path

from nhl_microstats.cards import card_data, top_players
from nhl_microstats.percentile import REFERENCE

TEMPLATE = Path(__file__).with_name("cards_template.html")


def write_cards(players=None, season="2025", out="cards.html", vs_season=REFERENCE) -> Path:
    data = card_data(players or top_players(season), season, vs_season)
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(data))
    if len(data["cards"]) == 1:
        html = html.replace("<title>NHL Microstat Cards</title>", f"<title>{data['cards'][0]['name']} Card</title>", 1)
    out = Path(out)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {len(data['cards'])} cards to {out.resolve()}")
    return out
