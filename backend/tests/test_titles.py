from app.library.titles import looks_untidy, tidy_title, title_key


def test_tidy_titles():
    assert tidy_title("last ninja_ the (by $olo1870) [easyflash]") == "The Last Ninja (EasyFlash)"
    assert tidy_title("Last Ninja_ The") == "The Last Ninja"
    assert tidy_title("Bruce Lee_ Return of Fury") == "Bruce Lee: Return of Fury"
    assert tidy_title("maniac mansion") == "Maniac Mansion"
    assert tidy_title("boulder dash iii") == "Boulder Dash III"
    assert tidy_title("Last Ninja II") == "Last Ninja II"  # already tidy: unchanged
    assert tidy_title("BRUCE LEE") == "BRUCE LEE"          # has capitals: left alone


def test_title_key_ignores_edition_and_article():
    assert title_key("The Last Ninja (EasyFlash)") == title_key("Last Ninja_ The") == title_key("last ninja")
    assert title_key("Last Ninja 2") != title_key("Last Ninja")


def test_looks_untidy():
    assert looks_untidy("maniac mansion") and looks_untidy("Last Ninja_ The") and not looks_untidy("The Last Ninja")
