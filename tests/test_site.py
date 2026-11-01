import datetime as dt


def test_current_season_starts_in_september():
    from pattullometrics.update import current_season
    assert current_season(dt.date(2026, 10, 18)) == 2026
    assert current_season(dt.date(2027, 4, 2)) == 2026
    assert current_season(dt.date(2027, 9, 1)) == 2027


def test_card_template_and_pages_ship():
    from pattullometrics import site
    from pattullometrics.cards import TEMPLATE
    assert "/*__DATA__*/null" in TEMPLATE.read_text(encoding="utf-8")
    for f in [site.INDEX_TEMPLATE, site.HOME_TEMPLATE, *(site.TEMPLATE.with_name(n) for n in
                                                        [*site.GOALIE_PAGES.values(), *site.TEAM_PAGES.values()])]:
        assert f.exists(), f
    assert len(site.code_hash()) == 64   # reads every module it depends on, here and in the stats packages
