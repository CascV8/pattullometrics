# Pattullometrics

The website at https://pattullometrics.pages.dev: player cards, the skater, goalie and team pages, the home page,
and the updater that rebuilds them as games come in. This repo is the UI; the numbers come from

- [nhl-microstats](https://github.com/CascV8/nhl-microstats): skater microstats, percentiles, card numbers,
  goalie ratings
- [team-metrics](https://github.com/CascV8/team-metrics): team metrics and ranks, and lineups
- [NHLe](https://github.com/CascV8/NHLe): the NHLe prospect cards (`nhle.html`), added at deploy

all installed from GitHub (`uv sync`).

```
uv run pattullometrics cards Buium --season 2026     # player cards as one HTML page
uv run team-metrics lineups                          # Daily Faceoff lineups for the team pages
```

## Website

The live site is built by GitHub Actions every 5 minutes and served by Cloudflare Pages
(`.github/workflows/update-site.yml`). `site` exports a static site (an index of every skater,
and a card page per player) and `watch` does the same job as the workflow on your own machine: every 5 minutes it checks the schedule, and once a game is
official it downloads it, refreshes Edge and bios, rebuilds the season, re-exports the site
and runs `--deploy`. See [docs/website.md](docs/website.md) for the design and hosting options.

```
uv run pattullometrics site                          # current + last complete season
uv run python -m http.server -d site 8000           # preview at http://localhost:8000
uv run pattullometrics update                        # one pass, e.g. from a scheduled task
uv run pattullometrics watch --deploy "<command that uploads site/>"
```

## Tests

```
uv run pytest
```
