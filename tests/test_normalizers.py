from datetime import datetime

from absulli.http.normalizers import (
    extract_author,
    normalize_history_payload,
    normalize_item_notification_context,
    normalize_library_payload,
    normalize_media_item_payload,
    normalize_online_payload,
    normalize_user_payload,
    parse_ts,
    safe_float,
    safe_text,
)


def test_safe_text_handles_nested_abs_shapes_and_lists():
    assert safe_text(None) == ""
    assert safe_text(123) == "123"
    assert safe_text({"displayName": "Friendly Name"}) == "Friendly Name"
    assert safe_text({"ignored": "x", "name": "Named"}, "missing") == "Named"
    assert safe_text([{"name": "One"}, "Two", None, {"model": "Three"}]) == "One, Two, Three"


def test_safe_float_and_parse_ts_are_tolerant():
    assert safe_float("12.5") == 12.5
    assert safe_float("bad", default=7) == 7
    assert safe_float(None, default=3) == 3

    seconds = parse_ts(1_700_000_000)
    millis = parse_ts(1_700_000_000_000)
    iso = parse_ts("2026-06-09T20:10:00Z")

    assert isinstance(seconds, datetime)
    assert seconds == millis
    assert iso == datetime(2026, 6, 9, 20, 10, 0)
    assert parse_ts("not-a-date") is None
    assert parse_ts(None) is None


def test_extract_author_supports_metadata_lists_and_fallback_fields():
    assert extract_author(
        {},
        {},
        {"metadata": {"authors": [{"name": "Vince Flynn"}, {"authorName": "Kyle Mills"}]}},
    ) == "Vince Flynn, Kyle Mills"

    assert extract_author(
        {"authorName": "Row Author"},
        {},
        {"metadata": {}},
    ) == "Row Author"


def test_normalize_history_payload_handles_abs_variants_and_clamps_progress():
    payload = {
        "listeningSessions": [
            {
                "sessionId": "session-1",
                "user": {"id": "user-1", "username": "raw-user"},
                "libraryItem": {
                    "id": "item-1",
                    "libraryId": "lib-1",
                    "mediaType": "book",
                    "media": {
                        "duration": 600,
                        "metadata": {
                            "title": "Transfer of Power",
                            "authors": [{"name": "Vince Flynn", "id": "author-1"}],
                        },
                    },
                },
                "displayTitle": "",
                "timeListening": 120,
                "currentTime": 900,
                "deviceInfo": {"deviceName": "Pixel", "model": "Pixel 9"},
                "client": {"name": "Audiobookshelf"},
                "startedAt": "2026-06-09T20:00:00Z",
                "updatedAt": "2026-06-09T20:10:00Z",
            },
            {"sessionId": ""},
            "not-a-row",
        ]
    }

    rows = normalize_history_payload(payload)

    assert len(rows) == 1
    row = rows[0]
    assert row["abs_session_id"] == "session-1"
    assert row["abs_user_id"] == "user-1"
    assert row["username"] == "raw-user"
    assert row["abs_item_id"] == "item-1"
    assert row["title"] == "Unknown"
    assert row["author"] == "Vince Flynn"
    assert row["media_type"] == "book"
    assert row["library_id"] == "lib-1"
    assert row["duration_seconds"] == 120
    assert row["current_time"] == 900
    assert row["progress"] == 100
    assert row["device_name"] == "Pixel"
    assert row["model"] == "Pixel 9"
    assert row["client"] == "Audiobookshelf"
    assert row["started_at"] == datetime(2026, 6, 9, 20, 0, 0)
    assert row["updated_at"] == datetime(2026, 6, 9, 20, 10, 0)


def test_normalize_online_payload_calculates_progress_and_device_fields():
    rows = normalize_online_payload(
        {
            "openSessions": [
                {
                    "id": "open-1",
                    "userId": "user-1",
                    "username": "raw-user",
                    "libraryItemId": "item-1",
                    "displayTitle": "The Fourth Option",
                    "mediaType": "book",
                    "duration": 1000,
                    "currentTime": 250,
                    "timeListening": 60,
                    "device": {"deviceName": "iPhone", "model": "iPhone 15"},
                    "player": {"name": "Mobile App"},
                    "ipAddress": {"ipAddress": "192.168.0.10"},
                }
            ]
        }
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["session_key"] == "open-1"
    assert row["abs_user_id"] == "user-1"
    assert row["title"] == "The Fourth Option"
    assert row["progress"] == 25
    assert row["device_name"] == "iPhone"
    assert row["model"] == "iPhone 15"
    assert row["client"] == "Mobile App"
    assert row["ip_address"] == "192.168.0.10"



def test_normalize_online_payload_ignores_users_online_without_playback_data():
    rows = normalize_online_payload(
        {
            "usersOnline": [
                {
                    "id": "user-1",
                    "username": "admin",
                    "displayName": "Admin",
                    "lastSeen": "2026-06-09T20:10:00Z",
                }
            ]
        }
    )

    assert rows == []


def test_normalize_online_payload_supports_nested_user_session_data():
    rows = normalize_online_payload(
        {
            "usersOnline": [
                {
                    "id": "user-1",
                    "username": "admin",
                    "currentListeningSession": {
                        "id": "session-1",
                        "libraryItemId": "item-1",
                        "displayTitle": "Active Book",
                        "mediaType": "book",
                        "duration": 500,
                        "currentTime": 125,
                    },
                }
            ]
        }
    )

    assert len(rows) == 1
    assert rows[0]["session_key"] == "session-1"
    assert rows[0]["abs_user_id"] == "user-1"
    assert rows[0]["username"] == "admin"
    assert rows[0]["title"] == "Active Book"
    assert rows[0]["progress"] == 25

def test_notification_language_codes_use_display_names():
    context = normalize_item_notification_context(
        {
            "id": "podcast-1",
            "mediaType": "podcast",
            "media": {"metadata": {"title": "Crime Junkie", "language": "en"}},
        }
    )
    assert context["language"] == "English"


def test_item_notification_context_reads_series_name():
    context = normalize_item_notification_context(
        {
            "id": "book-1",
            "mediaType": "book",
            "media": {
                "metadata": {
                    "title": "The Bourne Identity",
                    "seriesName": "Jason Bourne",
                }
            },
        }
    )

    assert context["series"] == "Jason Bourne"


def test_normalize_media_item_payload_extracts_metadata_and_filters_empty_ids():
    rows = normalize_media_item_payload(
        {
            "results": [
                {
                    "id": "item-1",
                    "mediaType": "book",
                    "media": {
                        "metadata": {
                            "title": "Consent to Kill",
                            "authors": [{"id": "author-1", "name": "Vince Flynn"}],
                            "narratorName": "George Guidall",
                            "seriesName": "Mitch Rapp",
                            "subtitle": "A Mitch Rapp Novel",
                            "publisher": "Atria",
                            "description": "<p>A <b>thriller</b>.<br /><br />Second paragraph.</p>",
                            "isbn": "9780743453998",
                            "asin": "B002V5D2J6",
                            "language": "English",
                            "itunesId": "1234567890",
                            "publishedYear": "2005",
                        },
                        "duration": 3600,
                        "coverPath": "/cover/item-1",
                    },
                    "sizeBytes": 12345,
                    "addedAt": "2026-06-01T12:00:00Z",
                },
                {"id": ""},
            ]
        },
        library_id="lib-1",
        library_name="Audiobooks",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["abs_item_id"] == "item-1"
    assert row["library_id"] == "lib-1"
    assert row["library_name"] == "Audiobooks"
    assert row["title"] == "Consent to Kill"
    assert row["author"] == "Vince Flynn"
    assert row["author_id"] == "author-1"
    assert row["narrator"] == "George Guidall"
    assert row["series"] == "Mitch Rapp"
    assert row["series_id"] == ""
    assert row["subtitle"] == "A Mitch Rapp Novel"
    assert row["publisher"] == "Atria"
    assert row["description"] == "A thriller.\n\nSecond paragraph."
    assert row["isbn"] == "9780743453998"
    assert row["asin"] == "B002V5D2J6"
    assert row["language"] == "English"
    assert row["itunes_id"] == "1234567890"
    assert row["year"] == "2005"
    assert row["duration"] == 3600
    assert row["size_bytes"] == 12345
    assert row["added_at"] == datetime(2026, 6, 1, 12, 0, 0)


def test_normalize_media_item_payload_reads_expanded_series_id():
    rows = normalize_media_item_payload(
        [
            {
                "id": "item-1",
                "media": {
                    "metadata": {
                        "title": "Consent to Kill",
                        "series": [{"id": "series-1", "name": "Mitch Rapp"}],
                    }
                },
            }
        ]
    )

    assert rows[0]["series"] == "Mitch Rapp"
    assert rows[0]["series_id"] == "series-1"


def test_normalize_user_and_library_payloads_are_defensive():
    users = normalize_user_payload(
        {
            "users": [
                {"id": "user-1", "username": "raw-user", "displayName": "Friendly", "isDisabled": False},
                "bad-row",
            ]
        }
    )
    libraries = normalize_library_payload(
        [
            {"id": "lib-1", "name": "Audiobooks", "mediaType": "book", "numItems": "3", "displayOrder": "2"},
            {"id": "", "name": "Missing"},
            "bad-row",
        ]
    )

    assert users == [
        {
            "abs_user_id": "user-1",
            "username": "raw-user",
            "display_name": "Friendly",
            "is_active": True,
        }
    ]
    assert libraries == [
        {
            "abs_library_id": "lib-1",
            "name": "Audiobooks",
            "media_type": "book",
            "item_count": 3,
            "display_order": 2,
        }
    ]


def test_display_language_normalizes_regional_english_code():
    from absulli.http.normalizers import display_language

    assert display_language("en-us") == "English (US)"
    assert display_language("en-gb") == "English (UK)"
