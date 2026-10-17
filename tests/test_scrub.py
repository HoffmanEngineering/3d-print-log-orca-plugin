"""Cases ported from the API's SlicerSettingsHelpersTests (SlicerSettingsScrubber)."""

import time

import pytest

from printlog_orca import scrub


@pytest.mark.parametrize(
    "key",
    [
        "print_host",
        "printhost_apikey",
        "printhost_password",
        "printhost_user",
        "PrintHost_CAFile",
        "bbl_use_printhost",
        "octoprint_api_key",
        "some_token",
        "cloud_secret",
        "post_process",
        "access_code",
    ],
)
def test_sensitive_keys(key):
    assert scrub.is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "layer_height",
        "host_type",
        "slowdown_for_curled_perimeters",
        "print_settings_id",
        "filament_settings_id",
    ],
)
def test_ordinary_keys(key):
    assert not scrub.is_sensitive_key(key)


def test_scrub_text_blanks_only_sensitive_values_and_keeps_every_other_character():
    text = (
        "; layer_height = 0.2\r\n;printhost_apikey=abc123\r\n; print_host: http://pi.local\n"
        "G1 X1 ; printhost_password = not-a-setting-line\n; password\n;  printhost_user  =  me  "
    )

    assert scrub.scrub_text(text) == (
        "; layer_height = 0.2\r\n;printhost_apikey=\r\n; print_host:\n"
        "G1 X1 ; printhost_password = not-a-setting-line\n; password\n;  printhost_user  ="
    )


@pytest.mark.parametrize("terminator", ["\n", "\r", "\r\n", "\f", "\u0085", "\u2028", "\u2029"])
def test_scrub_text_splits_lines_like_the_parser(terminator):
    lines = [
        "; layer_height = 0.2",
        "; printhost_apikey = secret",
        "; print_host = http://secret",
        "; nozzle_diameter = 0.4",
    ]

    scrubbed = scrub.scrub_text(terminator.join(lines))

    expected = ["; layer_height = 0.2", "; printhost_apikey =", "; print_host =", lines[3]]
    assert scrubbed == terminator.join(expected)


def test_scrub_text_does_not_end_a_line_at_other_unicode_breaks():
    # str.splitlines() also breaks at \v and \x1c-\x1e; the parser does not, so neither may we.
    text = "; layer_height = 0.2\v; printhost_apikey = secret"

    assert scrub.scrub_text(text) == text


def test_scrub_text_with_nothing_sensitive_returns_the_same_text():
    text = "; layer_height = 0.2\n; host_type = octoprint\n"

    assert scrub.scrub_text(text) is text


def test_scrub_text_is_fast_on_a_window_of_sensitive_lines():
    text = "; printhost_apikey = secret\n" * (4_000_000 // 28)

    started = time.perf_counter()
    scrubbed = scrub.scrub_text(text)

    assert "secret" not in scrubbed
    assert time.perf_counter() - started < 5


def test_scrub_preset_drops_sensitive_keys_and_keeps_the_order():
    preset = {
        "name": "My Printer",
        "print_host": "http://pi.local",
        "printhost_apikey": "abc",
        "inherits": "Prusa MK4S 0.4 nozzle",
        "post_process": ["C:\\Users\\me\\upload.exe --key 123"],
        "nozzle_diameter": ["0.4"],
    }

    scrubbed = scrub.scrub_preset(preset)

    assert list(scrubbed) == ["name", "inherits", "nozzle_diameter"]
    assert "print_host" in preset  # the input is left alone
