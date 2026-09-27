"""Free Wikimedia reference photos for the cover, and the subjects they are looked up for.

No network: every Wikidata and Commons answer is replayed from JSON recorded
from the public APIs (tests/fixtures/wikimedia), through a fake session.
"""
import copy
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests

from app.text_rules import EM_DASH, require_no_em_dash, require_readable_text
from app.tools import media_tools, reference_photos

FIXTURES = Path(__file__).parent / "fixtures" / "wikimedia"


def _key(url, params):
    return url + "?" + urlencode(sorted((params or {}).items()))


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return copy.deepcopy(self._data)


class FakeSession:
    """Answers GETs from recorded responses; an unrecorded request fails like the network."""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls: list[dict] = []
        self.missing: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}),
                           "timeout": timeout})
        key = _key(url, params)
        if key not in self.responses:
            self.missing.append(key)
            raise requests.ConnectionError(f"not recorded: {key}")
        return _Response(self.responses[key])


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _session(name):
    return FakeSession(_fixture(name)["responses"])


def _pages():
    (response,) = _fixture("files")["responses"].values()
    return {page["title"].split(":", 1)[1]: page for page in response["query"]["pages"]}


def _search_key(subject):
    return _key(reference_photos._COMMONS_API, {
        "action": "query", "format": "json", "formatversion": "2", "generator": "search",
        "gsrsearch": f"{subject} filetype:bitmap", "gsrnamespace": "6",
        "gsrlimit": str(reference_photos._SEARCH_LIMIT), **reference_photos._IMAGE_INFO,
    })


def _entity_search_key(subject):
    return _key(reference_photos._WIKIDATA_API, {
        "action": "wbsearchentities", "search": subject, "language": "en", "uselang": "en",
        "type": "item", "limit": str(reference_photos._ENTITY_HITS), "format": "json",
    })


# --- the Wikidata -> Commons chain -------------------------------------------


def test_the_prime_minister_is_found_through_his_wikidata_portraits(monkeypatch):
    """The BBC story: Albanese's P18 portraits first, then a search for the agency."""
    session = _session("albanese")
    report: dict = {}
    found = reference_photos.reference_candidates(
        ["Anthony Albanese", "Services Australia"], session=session, report=report,
    )
    assert not session.missing
    assert found and all(c["origin"] == "wikimedia" for c in found)
    top = found[0]
    assert (top["subject"], top["role"], top["licence"]) == ("Anthony Albanese", "subject", "CC BY 4.0")
    assert "Anthony_Albanese_Official_Portrait" in top["url"]  # the 1981x2444 original
    assert top["context_url"].startswith("https://commons.wikimedia.org/wiki/File:")
    assert top["about"] == "Anthony Albanese: 31st prime minister of Australia"
    government = [c for c in found if c["credit"] == (
        "Australian Government / Wikimedia Commons, CC BY 4.0 (cropped, text added)")]
    assert government and (government[0]["width"], government[0]["height"]) == (1677, 2237)
    # Two photos per subject at most; the agency's own photos are CC BY-SA,
    # and the "Broome RSL" file (in "Returned and Services League of
    # Australia") does not name it.
    assert [(c["subject"], c["role"]) for c in found] == [("Anthony Albanese", "subject")] * 2
    assert report["share_alike_skipped"] >= 1
    # Albanese had usable Wikidata photos, so no keyword search was spent on him.
    assert _search_key("Anthony Albanese") not in [_key(c["url"], c["params"]) for c in session.calls]

    monkeypatch.setattr(reference_photos, "_ALLOW_SHARE_ALIKE", True)
    found = reference_photos.reference_candidates(
        ["Anthony Albanese", "Services Australia"], session=_session("albanese"),
    )
    # Every Albanese photo ranks before the agency's search results.
    roles = [(c["subject"], c["role"]) for c in found]
    first_search = roles.index(("Services Australia", "search"))
    assert all(subject == "Anthony Albanese" for subject, _ in roles[:first_search])
    assert all(role == "search" for _, role in roles[first_search:])
    assert len(roles) - first_search <= reference_photos._PER_SUBJECT
    # The search also matched "Lake MacKay Australia" and fur seals: only files
    # that name the agency are offered, in the search's own order.
    names = [c["url"].split("Services_Australia_")[1][:2] for c in found[first_search:]]
    assert names == ["01", "02"]


def test_subjects_take_turns_two_photos_each():
    """The first subject's files used to fill every slot."""
    def cand(index, role, n):
        return (index, {"url": f"https://u/{index}-{role}-{n}.jpg", "role": role,
                        "_sort": (reference_photos._ROLE_ORDER[role], n, 0, 0)})

    found = [cand(0, "subject", n) for n in range(4)] + [cand(1, "search", 0), cand(1, "search", 1),
                                                          cand(2, "leader", 0)]
    urls = [c["url"] for c in reference_photos._in_turns(found, 6)]
    assert urls == [
        "https://u/0-subject-0.jpg", "https://u/2-leader-0.jpg", "https://u/0-subject-1.jpg",
        "https://u/1-search-0.jpg", "https://u/1-search-1.jpg",
    ]


def test_every_request_names_the_project_and_never_a_person():
    session = _session("xiaomi")
    reference_photos.reference_candidates(["Xiaomi"], session=session)
    agents = {call["headers"].get("User-Agent") for call in session.calls}
    assert agents == {media_tools.WIKIMEDIA_USER_AGENT}
    assert "@" not in media_tools.WIKIMEDIA_USER_AGENT and "http" in media_tools.WIKIMEDIA_USER_AGENT


def test_a_company_brings_its_headquarters_and_its_founder():
    """Xiaomi's P18 photos, then Lei Jun (its CEO, chair and founder) as a leader."""
    session = _session("xiaomi")
    report: dict = {}
    found = reference_photos.reference_candidates(["Xiaomi"], session=session, report=report)
    assert not session.missing
    assert [(c["role"], c["licence"]) for c in found] == [
        ("subject", "CC BY 4.0"),      # Xiaomi Headquarters
        ("leader", "CC BY 4.0"),       # Lei Jun; the CC BY-SA store photo is left out
    ]
    assert report == {"share_alike_skipped": 1}
    assert "Xiaomi_Headquarters" in found[0]["url"]
    assert found[0]["about"] == "Xiaomi: Chinese electronics company"
    leader = found[1]
    assert leader["about"] == "Lei Jun, a leader or founder of Xiaomi: Chinese electronics company"
    assert leader["subject"] == "Lei Jun (Xiaomi)"
    assert "Lei Jun" in leader["reason"] and "Xiaomi" in leader["reason"]
    # A 628x836 original is served unscaled, whatever size the API advertises.
    assert (leader["width"], leader["height"]) == (628, 836)


def test_the_chief_executive_comes_before_the_chair():
    """Sam Altman (P169) before Bret Taylor (P488), though Taylor's photo is larger."""
    session = _session("openai")
    found = reference_photos.reference_candidates(["OpenAI"], session=session)
    assert not session.missing
    # The Pioneer Building (its headquarters) is CC BY-SA: left out by default.
    assert [(c["role"], c["subject"]) for c in found] == [
        ("leader", "Sam Altman (OpenAI)"),
        ("leader", "Bret Taylor (OpenAI)"),
    ]
    assert found[0]["height"] < found[1]["height"]


def test_an_ambiguous_word_is_not_taken_for_the_story_subject():
    """'Mistral' is the exact label of a warship, a submarine and a program."""
    hits = {"search": [
        {"id": "Q2981505", "label": "Mistral", "description": "2004 Mistral-class amphibious assault ship"},
        {"id": "Q63140083", "label": "Mistral", "description": "family name"},
        {"id": "Q6019550", "label": "Mistral", "description": "attack submarine"},
        {"id": "Q3316995", "label": "Mistral", "description": "software"},
    ]}
    session = FakeSession({_entity_search_key("Mistral"): hits})
    assert reference_photos.reference_candidates(["Mistral"], session=session) == []
    assert not session.missing
    assert not [c for c in session.calls if c["params"].get("action") == "wbgetentities"]
    # Nor searched: on Commons, 'Mistral' is a warship and a pair of Mercury craters.
    assert not [c for c in session.calls if c["params"].get("generator") == "search"]


_NO_FILES = {"query": {"pages": []}}


@pytest.mark.parametrize("subject, hits, searched", [
    # One word Wikidata cannot place as a person, body or place: its
    # namesakes (a radio technique, a street sign) are all Commons would find.
    ("MiMo", [{"id": "Q176862", "label": "MIMO", "description": "use of multiple antennas in radio"}], False),
    ("GPT", [
        {"id": "Q116777014", "label": "generative pre-trained transformer", "aliases": ["GPT"],
         "description": "type of large language model"},
        {"id": "Q603889", "label": "GUID Partition Table", "aliases": ["GPT"],
         "description": "standard for the layout of the partition table"},
    ], False),
    ("UltraSpeed", [{"id": "Q68111445", "label": "Ultraspeed versus Ektaspeed X-ray film",
                     "description": "scientific article published on 01 December 1988"}], False),
    ("Qwerty", [], False),
    # Placed as a company: its own name is searched when its items give no photo.
    ("Xiaomi", [{"id": "Q1636958", "label": "Xiaomi", "description": "Chinese electronics company"}], True),
    # Two words or more narrow a search down enough on their own.
    ("Voxtral TTS", [], True),
    ("Liquid AI", [], True),
])
def test_a_one_word_subject_is_searched_only_when_wikidata_places_it(subject, hits, searched):
    responses = {_entity_search_key(subject): {"search": hits}, _search_key(subject): _NO_FILES}
    for hit in hits:
        responses[_key(reference_photos._WIKIDATA_API, {
            "action": "wbgetentities", "ids": hit["id"], "props": "claims", "format": "json",
        })] = {"entities": {hit["id"]: {"claims": {}}}}
    session = FakeSession(responses)
    assert reference_photos.reference_candidates([subject], session=session) == []
    assert not session.missing
    made = [c for c in session.calls if c["params"].get("generator") == "search"]
    assert bool(made) is searched
    # A scientific article is never taken for the subject's item.
    if subject == "UltraSpeed":
        assert not [c for c in session.calls if c["params"].get("action") == "wbgetentities"]


def test_a_search_result_must_name_every_word_of_the_subject():
    assert reference_photos._names_subject("Liquid AI office in Boston.jpg", "Liquid AI")
    assert not reference_photos._names_subject("Liquid nitrogen in a flask.jpg", "Liquid AI")
    assert not reference_photos._names_subject("Services counter.jpg", "Services Australia")


def test_screenshots_and_small_files_leave_only_the_real_photo():
    """Mistral AI's own P18 is a chat screenshot; its CEO's portrait is what is left."""
    session = _session("mistral")
    found = reference_photos.reference_candidates(["Mistral AI"], session=session)
    assert not session.missing
    assert [(c["role"], c["subject"], c["licence"]) for c in found] == [
        ("leader", "Arthur Mensch (Mistral AI)", "CC0"),
    ]


def test_a_commons_search_never_offers_ai_generated_art():
    """With no Wikidata item, the search finds 'Mistral AI representation (FLUX Pro)'."""
    responses = dict(_fixture("mistral")["responses"])
    responses[_entity_search_key("Mistral AI")] = {"search": []}
    session = FakeSession(responses)
    found = reference_photos.reference_candidates(["Mistral AI"], session=session)
    assert not session.missing
    # FLUX art, chat screenshots, a 500 px drawing and a "Le Chat" UI capture
    # whose name does not even say Mistral: nothing there is a cover.
    assert found == []


# --- what is never offered ------------------------------------------------------


def test_ai_art_logos_charts_and_small_files_are_dropped():
    pages = _pages()
    flux = pages["Mistral AI representation (FLUX Pro).webp"]
    info = flux["imageinfo"][0]
    assert media_tools.image_fits_cover(info["width"], info["height"])  # big enough...
    assert reference_photos._candidate(flux, "Mistral AI", "search") is None  # ...but made by Flux
    assert reference_photos._candidate(pages["Xiaomi logo (2021-).svg"], "Xiaomi", "search") is None

    chart = pages["Approval Rating of Anthony Albanese, 23 May 2022 to 21 June 2022.png"]
    info = chart["imageinfo"][0]
    assert media_tools.image_fits_cover(info["width"], info["height"])
    assert reference_photos._candidate(chart, "Anthony Albanese", "search") is None

    small = pages["Lei Jun (2026) 02.jpg"]  # 390x573
    assert reference_photos._candidate(small, "Lei Jun", "search") is None
    resized = copy.deepcopy(pages["Arthur Mensch.png"])
    resized["imageinfo"][0].update(width=459, height=675)
    assert reference_photos._candidate(resized, "Arthur Mensch", "subject") is None

    screenshot = pages["Le Chat chatbot memories screenshot.webp"]
    assert reference_photos._candidate(screenshot, "Mistral AI", "subject") is None

    pioneer = pages["Pioneer Building, San Francisco (2019) -1.jpg"]
    assert reference_photos._candidate(pioneer, "OpenAI", "subject") is None  # share-alike is off
    kept = reference_photos._candidate(pioneer, "OpenAI", "subject", allow_share_alike=True)
    assert kept is not None and kept["licence"] == "CC BY-SA 4.0"
    assert kept["credit"].endswith("CC BY-SA 4.0 (cropped, text added)")


def test_an_ai_generated_file_name_is_dropped_even_without_a_category():
    page = copy.deepcopy(_pages()["Arthur Mensch.png"])
    page["title"] = "File:ChatGPT Image Sep 19, 2026 portrait.png"
    page["categories"] = []
    page["imageinfo"][0]["extmetadata"]["Categories"] = {"value": ""}
    assert reference_photos._candidate(page, "Arthur Mensch", "subject") is None


def test_the_thumbnail_url_is_used_exactly_as_the_api_gave_it():
    """A self-built /NNNpx/ URL returns HTTP 400; the API's own thumburl works."""
    page = _pages()["Anthony Albanese Official Portrait (cropped).jpg"]
    candidate = reference_photos._candidate(page, "Anthony Albanese", "subject")
    assert candidate["url"] == page["imageinfo"][0]["thumburl"]
    assert (candidate["width"], candidate["height"]) == (1920, 2369)

    bare = copy.deepcopy(page)
    for key in ("thumburl", "thumbwidth", "thumbheight"):
        bare["imageinfo"][0].pop(key)
    candidate = reference_photos._candidate(bare, "Anthony Albanese", "subject")
    assert candidate["url"] == page["imageinfo"][0]["url"]
    assert (candidate["width"], candidate["height"]) == (1981, 2444)


# --- credit and licence -------------------------------------------------------


def _with(artist=None, licence=None, nonfree=None):
    page = copy.deepcopy(_pages()["Arthur Mensch.png"])
    meta = page["imageinfo"][0]["extmetadata"]
    if artist is not None:
        meta["Artist"] = {"value": artist}
    if licence is not None:
        meta["LicenseShortName"] = {"value": licence}
    if nonfree is not None:
        meta["NonFree"] = {"value": nonfree}
    return page


def test_the_credit_is_plain_house_style_text():
    artist = (
        '<a href="//commons.wikimedia.org/wiki/User:X" title="User:X">Jane “JJ” Doe</a>'
        " — Studio &amp; Co – <b>Department</b> of Very Long Names That Keep Going On And On Forever"
    )
    candidate = reference_photos._candidate(_with(artist=artist, licence="CC BY 4.0"), "X", "subject")
    credit = candidate["credit"]
    assert credit.startswith('Jane "JJ" Doe - Studio & Co - Department of')
    assert credit.endswith(" / Wikimedia Commons, CC BY 4.0 (cropped, text added)")
    assert EM_DASH not in credit and "<" not in credit and "&amp;" not in credit
    assert len(credit.split(" / Wikimedia Commons")[0]) <= 80
    require_no_em_dash(credit)
    require_readable_text(credit)


def test_an_unreadable_artist_falls_back_to_the_bare_credit():
    candidate = reference_photos._candidate(_with(artist="asdfgh uploads", licence="CC0"), "X", "subject")
    assert candidate["credit"] == "Wikimedia Commons, CC0"
    candidate = reference_photos._candidate(_with(artist="", licence="Public domain"), "X", "subject")
    assert candidate["credit"] == "Wikimedia Commons, Public domain"


@pytest.mark.parametrize("licence, rank", [
    ("CC0", 0), ("Public domain", 0), ("PD-US", 0), ("PD-USGov", 0),
    ("CC BY 4.0", 1), ("CC BY 2.0", 1), ("CC BY 3.0 igo", 1), ("CC BY 2.0 de", 1),
    ("Attribution", 1), ("OGL v3", 1), ("OGL 3", 1), ("GODL-India", 1),
    ("CC BY-SA 4.0", 2), ("CC BY-SA 3.0 igo", 2), ("CC-BY-SA-4.0", 2),
    # Not on the allowlist: the whole licence text or a copyleft the post cannot carry.
    ("GFDL 1.2", None), ("GFDL", None), ("GPL", None), ("LGPL", None), ("FAL", None),
    ("Free Art License", None),
    ("CC BY-NC 4.0", None), ("CC BY-NC-SA 4.0", None), ("CC BY-ND 2.0", None),
    ("CC BY-NC-ND 3.0", None), ("", None), ("Fair use", None), ("Some custom licence", None),
])
def test_only_allowlisted_licences_are_used(licence, rank):
    assert reference_photos._licence_class(licence) == rank
    # Share-alike is off until the owner decides.
    assert reference_photos._licence_rank(licence) == (None if rank == 2 else rank)
    assert reference_photos._licence_rank(licence, allow_share_alike=True) == rank


def test_share_alike_is_off_by_default():
    from app.config import Settings

    assert Settings().cover_allow_share_alike is False
    assert reference_photos._ALLOW_SHARE_ALIKE is False


def test_public_domain_needs_no_changes_note():
    assert reference_photos._credit("Jane Doe", "CC0") == "Jane Doe / Wikimedia Commons, CC0"
    assert reference_photos._credit("Jane Doe", "CC BY 4.0") == (
        "Jane Doe / Wikimedia Commons, CC BY 4.0 (cropped, text added)")


def _licence_search(licences, subject="Jane Person", mime="image/jpeg"):
    """A Commons search answer: the same portrait under several licences."""
    base = _pages()["Arthur Mensch.png"]
    pages = []
    for index, licence in enumerate(licences, start=1):
        page = copy.deepcopy(base)
        page["title"] = f"File:{subject} photo {index}.jpg"
        page["index"] = index
        info = page["imageinfo"][0]
        info["mime"] = mime
        info["thumburl"] = f"https://thumb.example/{index}.jpg"
        info["descriptionurl"] = f"https://commons.wikimedia.org/wiki/File:Person_photo_{index}.jpg"
        info["extmetadata"]["LicenseShortName"] = {"value": licence}
        pages.append(page)
    return {"query": {"pages": pages}}


def _licence_session(licences, **kwargs):
    return FakeSession({
        _entity_search_key("Jane Person"): {"search": []},
        _search_key("Jane Person"): _licence_search(licences, **kwargs),
    })


def test_search_results_keep_the_search_order_and_skip_share_alike():
    """A CC0 electrical panel once ranked first for "Mistral Small" by licence order."""
    session = _licence_session(["CC BY 4.0", "CC BY-SA 4.0", "CC BY-NC 4.0", "CC0", "CC BY-ND 2.0"])
    report: dict = {}
    found = reference_photos.reference_candidates(["Jane Person"], session=session, report=report)
    assert [c["licence"] for c in found] == ["CC BY 4.0", "CC0"]
    assert report == {"share_alike_skipped": 1}


def test_share_alike_can_be_switched_on(monkeypatch):
    monkeypatch.setattr(reference_photos, "_ALLOW_SHARE_ALIKE", True)
    found = reference_photos.reference_candidates(
        ["Jane Person"], session=_licence_session(["CC BY-SA 4.0", "CC BY 4.0"])
    )
    assert [c["licence"] for c in found] == ["CC BY-SA 4.0", "CC BY 4.0"]


def test_a_keyword_search_offers_only_jpeg_photos():
    session = _licence_session(["CC BY 4.0"], mime="image/png")
    assert reference_photos.reference_candidates(["Jane Person"], session=session) == []


def test_non_free_files_are_never_offered():
    page = _with(licence="CC BY 4.0", nonfree="true")
    assert reference_photos._candidate(page, "X", "subject") is None


# --- it never hangs and never raises ------------------------------------------


class _TimingOut:
    def __init__(self, delay):
        self.delay = delay
        self.timeouts = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.timeouts.append(timeout)
        time.sleep(self.delay)
        raise requests.ReadTimeout("read timed out")


def test_a_timing_out_api_returns_nothing_within_the_budget():
    session = _TimingOut(0.05)
    started = time.monotonic()
    found = reference_photos.reference_candidates(
        ["Anthony Albanese", "Services Australia"], budget_s=0.8, session=session
    )
    assert found == []
    assert time.monotonic() - started < 2.0
    assert session.timeouts and all(max(t) <= 0.8 for t in session.timeouts)


def test_an_unexpected_failure_returns_nothing():
    class Broken:
        def get(self, *_a, **_k):
            raise RuntimeError("boom")

    assert reference_photos.reference_candidates(["Xiaomi"], session=Broken()) == []
    assert reference_photos.reference_candidates([], session=Broken()) == []


def test_only_four_subjects_are_looked_up():
    session = FakeSession({})
    reference_photos.reference_candidates(["A1", "B2", "C3", "D4", "E5"], budget_s=5, session=session)
    searched = {call["params"].get("search") for call in session.calls if call["params"].get("search")}
    assert searched == {"A1", "B2", "C3", "D4"}


# --- story_subjects -------------------------------------------------------------

# The research brief of run-3c4107e390df ("this week major ai updates like new
# model launches and the comparative open weight models"), first facts.
_BRIEF_3C41 = {
    "summary": (
        "The week’s clearest model-launch theme is a split between efficient proprietary "
        "models and increasingly capable downloadable models. OpenAI announced GPT‑5.4 mini "
        "and nano on March 17, 2026 for fast coding and subagent workloads, while Mistral "
        "released the Apache 2.0–licensed Mistral Small 4 on March 16 with 119B total "
        "parameters but only 6.5B active per token. Mistral’s launch is especially notable "
        "because it combines text-and-image input, a 256k context window, and low API pricing "
        "with published throughput and coding comparisons against its predecessor and GPT‑OSS "
        "120B. Mistral also launched the 4B-parameter Voxtral TTS on March 23, while Hugging Face "
        "OpenEvals data provides a useful broader open-weight comparison point through "
        "DeepSeek‑V3.2’s benchmark results."
    ),
    "key_facts": [
        {"fact": "OpenAI announced “GPT‑5.4 mini and nano” on March 17, 2026."},
        {"fact": "OpenAI describes GPT‑5.4 mini and GPT‑5.4 nano as fast, efficient models optimized for coding and subagents."},
        {"fact": "Mistral announced Mistral Small 4 on March 16, 2026."},
        {"fact": "Mistral Small 4 is released under the Apache 2.0 license."},
        {"fact": "Mistral Small 4 has 119B total parameters and 6.5B active parameters per token."},
        {"fact": "Mistral Small 4 has a 256k context window and accepts text and image inputs."},
        {"fact": "Mistral Small 4 unifies instruct, reasoning, and coding capabilities in one model."},
        {"fact": "Mistral reports a 40% reduction in end-to-end completion time for Mistral Small 4 in a latency-optimized setup."},
    ],
}
_BBC_TITLE = "Why did an OpenAI system hack Australia's health system - and can it be stopped in the future?"


def test_subjects_of_the_ai_launch_brief_skip_months_and_rank_names():
    news = {"title": "this week major ai updates like new model launches and the comparative open weight models"}
    subjects = reference_photos.story_subjects(news, _BRIEF_3C41, limit=6)
    assert subjects[0] == "Mistral Small"
    assert {"Mistral", "OpenAI"} <= set(subjects)
    lowered = {s.lower() for s in subjects}
    assert not lowered & {"march", "the", "this", "week", "new"}
    assert all(not s.lower().startswith(("march", "the ")) for s in subjects)


def test_subjects_of_the_bbc_title():
    assert reference_photos.story_subjects({"title": _BBC_TITLE}) == ["OpenAI", "Australia"]


def test_a_title_without_case_says_nothing():
    assert reference_photos.story_subjects({"title": "this week major ai updates"}) == []
    shouting = {"title": "AI MODELS RACE ON SPEED AND OPEN WEIGHTS on this 25th September 2026"}
    assert reference_photos.story_subjects(shouting) == []


def test_a_surname_counts_toward_the_full_name_and_titles_are_dropped():
    research = {
        "summary": "Australian Prime Minister Anthony Albanese said the agent accessed public files.",
        "key_facts": [
            {"fact": "Services Australia reported the incident to the Australian Signals Directorate."},
            {"fact": "Albanese announced a taskforce review on 15 September 2026."},
            {"fact": "Albanese called the breach 'obviously unacceptable'."},
            {"fact": "OpenAI said it found the breach in August."},
        ],
    }
    subjects = reference_photos.story_subjects({"title": _BBC_TITLE}, research, limit=8)
    assert subjects[:2] == ["Anthony Albanese", "OpenAI"]  # 1 full + 2 surname mentions
    assert "Albanese" not in subjects and "Prime Minister" not in " ".join(subjects)
    assert "Australian" not in subjects  # a nationality alone is nobody to photograph
    # 'Australia' in the headline came before 'Services Australia': not its short form.
    assert "Australia" in subjects and "Services Australia" in subjects
    # On a tie, multi-word names first.
    assert subjects.index("Services Australia") < subjects.index("Australia")


def test_scraped_page_menus_are_not_subjects():
    news = {
        "title": _BBC_TITLE,
        "summary": "Skip to content\nHome\nNews\nSport\nBusiness\nTechnology\nHealth\n"
                   "An OpenAI agent has gone rogue and infiltrated an Australian government website.",
    }
    subjects = reference_photos.story_subjects(news, limit=8)
    assert subjects[0] == "OpenAI"
    assert not {"Home", "News", "Sport", "Business", "Technology", "Health", "Skip"} & set(subjects)


# --- which item a subject names (always-real-image critique, item 4) --------------


def _claims_key(ids):
    return _key(reference_photos._WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(ids), "props": "claims", "format": "json",
    })


def test_a_first_hit_that_is_not_the_subject_is_never_taken():
    hits = {"search": [{"id": "Q1", "label": "Medicare Benefits Schedule",
                        "description": "list of health services funded by Medicare"}]}
    session = FakeSession({_entity_search_key("Medicare Statistics Portal"): hits})
    assert reference_photos._entity(
        reference_photos._Api(session, 5), "Medicare Statistics Portal"
    ) == ("", False, "")


def test_namesakes_are_told_apart_by_the_story():
    """'Medicare' is the US programme and Australia's scheme; the BBC story is Australian."""
    hits = {"search": [
        {"id": "Q772467", "label": "Medicare",
         "description": "national social insurance program in the United States"},
        {"id": "Q1545787", "label": "Medicare",
         "description": "publicly funded universal health care insurance scheme in Australia"},
    ]}
    session = FakeSession({_entity_search_key("Medicare"): hits})
    story = frozenset(reference_photos._context_words(
        "An OpenAI agent reached a Medicare portal of the Australian health system in Australia"))
    entity_id, _placed, description = reference_photos._entity(
        reference_photos._Api(session, 5), "Medicare", story)
    assert entity_id == "Q1545787" and "Australia" in description


def test_an_accented_label_is_still_the_subject():
    hits = {"search": [{"id": "Q9", "label": "Clément Delangue", "description": "French entrepreneur"}]}
    session = FakeSession({_entity_search_key("Clement Delangue"): hits})
    assert reference_photos._entity(reference_photos._Api(session, 5), "Clement Delangue")[0] == "Q9"


# --- official websites and credits for files found elsewhere --------------------


def test_official_sites_come_from_wikidata_p856():
    hits = {"search": [{"id": "Q1636958", "label": "Xiaomi", "description": "Chinese electronics company"}]}
    site = {"mainsnak": {"datavalue": {"value": "https://www.mi.com/"}}, "rank": "normal"}
    session = FakeSession({
        _entity_search_key("Xiaomi"): hits,
        _claims_key(["Q1636958"]): {"entities": {"Q1636958": {"claims": {"P856": [site]}}}},
    })
    assert reference_photos.official_sites(["Xiaomi"], session=session) == ["https://www.mi.com/"]
    assert not session.missing


def _file_session(project, name, licence, nonfree=None):
    page = copy.deepcopy(_pages()["Arthur Mensch.png"])
    page["title"] = f"File:{name}"
    meta = page["imageinfo"][0]["extmetadata"]
    meta["LicenseShortName"] = {"value": licence}
    meta["Artist"] = {"value": "Jane Doe"}
    if nonfree:
        meta["NonFree"] = {"value": "true"}
    api = (reference_photos._COMMONS_API if project == "commons"
           else f"https://{project}.wikipedia.org/w/api.php")
    return FakeSession({_key(api, {
        "action": "query", "format": "json", "formatversion": "2",
        "titles": f"File:{name}", **reference_photos._IMAGE_INFO,
    }): {"query": {"pages": [page]}}})


def test_a_wikimedia_file_found_elsewhere_gets_its_credit():
    url = ("https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/"
           "Xiaomi_Headquarters_%28Beijing%29.jpg/1280px-Xiaomi_Headquarters_%28Beijing%29.jpg")
    session = _file_session("commons", "Xiaomi_Headquarters_(Beijing).jpg", "CC BY 4.0")
    found = reference_photos.file_credit(url, session=session)
    assert not session.missing
    assert found["reusable"] is True
    assert found["credit"] == "Jane Doe / Wikimedia Commons, CC BY 4.0 (cropped, text added)"
    assert found["context_url"]


def test_a_non_free_wikipedia_file_is_not_reusable():
    """en.wikipedia's local files are logos and posters under fair use."""
    url = "https://upload.wikimedia.org/wikipedia/en/4/4d/OpenAI_Logo.png"
    found = reference_photos.file_credit(
        url, session=_file_session("en", "OpenAI_Logo.png", "Fair use", nonfree=True))
    assert found["reusable"] is False and found["credit"] == ""
    assert reference_photos.file_credit("https://cdn.example.com/a.jpg") is None


# --- time budget (item 9) ---------------------------------------------------------


def test_requests_are_short_and_never_retried_near_the_budget():
    session = _TimingOut(0.0)
    api = reference_photos._Api(session, 30)
    assert api.get(reference_photos._WIKIDATA_API, {"action": "x"}) is None
    assert session.timeouts == [(5, 10), (5, 10)]  # one retry while time is left

    late = _TimingOut(0.0)
    api = reference_photos._Api(late, reference_photos._RETRY_MIN_S - 1)
    assert api.get(reference_photos._WIKIDATA_API, {"action": "x"}) is None
    assert len(late.timeouts) == 1


def test_a_name_the_hook_repeats_goes_first_even_when_shouted():
    """run-aa99162e2fe0: the hook was 'XIAOMI CHARGES 10X MORE FOR FASTER AI'."""
    research = {"summary": "MiMo-V2.6 from MiMo beats MiMo-V2 as Xiaomi prices MiMo up.",
                "key_facts": [{"fact": "MiMo is Xiaomi's model family."}]}
    plan = {"hook_title": "XIAOMI CHARGES 10X MORE FOR FASTER AI", "slides": []}
    assert reference_photos.story_subjects({"title": "ai news"}, research)[0] == "MiMo"
    assert reference_photos.story_subjects({"title": "ai news"}, research, plan)[0] == "Xiaomi"
    bbc = {"hook_title": "OPENAI'S TEST AI HACKED A MEDICARE PORTAL"}
    assert reference_photos.story_subjects({"title": _BBC_TITLE}, None, bbc)[0] == "OpenAI"


@pytest.mark.parametrize("name", [
    "Silicon Valley campus.jpg", "Xiaomi flagship store Beijing.jpg", "Photograph of the CEO.jpg",
    "Mapletree offices.jpg",
])
def test_file_names_are_filtered_on_whole_words(name):
    """'icon' in silicon, 'flag' in flagship, 'graph' in Photograph are not logos or charts."""
    assert not reference_photos._NOT_A_PHOTO_RE.search(name)
    assert reference_photos._NOT_A_PHOTO_RE.search("Xiaomi logo (2021).png")


def test_the_subjects_own_name_does_not_single_out_a_namesake():
    """'2004 Mistral-class amphibious assault ship' shares 'Mistral' with every Mistral story."""
    hits = {"search": [
        {"id": "Q2981505", "label": "Mistral", "description": "2004 Mistral-class amphibious assault ship"},
        {"id": "Q6019550", "label": "Mistral", "description": "attack submarine"},
    ]}
    session = FakeSession({_entity_search_key("Mistral"): hits})
    story = frozenset(reference_photos._context_words(
        "Mistral released Mistral Small 4, a Mistral model with 119B parameters"))
    assert reference_photos._entity(reference_photos._Api(session, 5), "Mistral", story)[0] == ""
