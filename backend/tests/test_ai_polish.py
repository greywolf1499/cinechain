"""Phase 27d: AI March Madness commentary and AI-generated Bounty Board bounties."""

import json

import httpx
import pytest
import respx

from app.services import bounties, llm
from app.services.bounties import MovieFacts
from tests.test_bounty_split import log, mock_film, rules_of, set_board
from tests.test_bounty_split import make_run as make_bounty_run
from tests.test_graph_mutators import client, db_engine
from tests.test_march_madness import advance, create, detail, mock_films
from tests.test_march_madness import make_run as make_bracket

__all__ = ["client", "db_engine"]

OLLAMA_CHAT = "http://localhost:11434/api/chat"
FIRST = "round_of_16-1"  # films 1 vs 2


@pytest.fixture(autouse=True)
def _clean_llm_state():
    llm.clear_cache()
    yield
    llm.clear_cache()


def enable_llm(client):
    resp = client.patch("/api/settings/integrations", json={"llm_provider": "ollama"})
    assert resp.status_code == 200, resp.text


def mock_reply(text) -> respx.Route:
    payload = text if isinstance(text, str) else json.dumps(text)
    return respx.post(OLLAMA_CHAT).mock(
        return_value=httpx.Response(
            200, json={"message": {"role": "assistant", "content": payload}}
        )
    )


def commentary(client, run_id, matchup_id=FIRST):
    return client.post(f"/api/runs/{run_id}/bracket/commentary", json={"matchup_id": matchup_id})


# --- Tale of the Tape ---


async def test_the_model_off_means_no_commentary():
    assert (
        await llm.generate_matchup_commentary({"title": "A"}, {"title": "B"}, llm.LlmConfig()) == ""
    )


def test_tape_uses_template_and_is_stored_while_the_model_is_off(client):
    with respx.mock:
        mock_films()
        run_id = make_bracket(client)
    answer = commentary(client, run_id)
    assert answer.status_code == 200
    assert answer.json()["tape"]["source"] == "template"
    assert len(answer.json()["tape"]["axes"]) == 4
    assert answer.json()["commentary"]
    assert detail(client, run_id)["rules_config"]["bracket_tape"][FIRST] == answer.json()["tape"]


def test_commentary_is_generated_once_and_cached_on_the_run(client):
    with respx.mock:
        mock_films()
        run_id = make_bracket(client)
        enable_llm(client)
        route = mock_reply({"headline": "Film 1 and Film 2 share Origin, but differ on Scale."})
        first = commentary(client, run_id)
        second = commentary(client, run_id)
    assert (
        "Film 1" in first.json()["commentary"]
        and first.json()["tape"]["source"] == "ai"
        and not first.json()["cached"]
    )
    assert (
        second.json()["commentary"] == first.json()["commentary"]
        and second.json()["cached"] is True
    )
    assert route.call_count == 1
    prompt = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    assert "Film 1" in prompt and "Film 2" in prompt and "Plot 1" in prompt
    stored = detail(client, run_id)["rules_config"]["bracket_tape"]
    assert stored == {FIRST: first.json()["tape"]}


def test_commentary_survives_the_matchup_being_decided(client):
    with respx.mock:
        mock_films()
        run_id = make_bracket(client)
        enable_llm(client)
        mock_reply("not valid structured output")
        first = commentary(client, run_id)
        assert advance(client, run_id, FIRST, 1).status_code == 200
    assert (
        detail(client, run_id)["rules_config"]["bracket_commentary"][FIRST]
        == first.json()["commentary"]
    )


def test_commentary_needs_a_known_filled_matchup(client):
    with respx.mock:
        mock_films()
        run_id = make_bracket(client)
        enable_llm(client)
        mock_reply("Hype!")
        assert commentary(client, run_id, "nonsense-9").status_code == 404
        assert (
            commentary(client, run_id, "quarterfinals-1").status_code == 409
        )  # nobody's in it yet


def test_a_failing_model_uses_the_template_tape(client):
    with respx.mock:
        mock_films()
        run_id = make_bracket(client)
        enable_llm(client)
        respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(500, json={"error": "boom"}))
        generated = commentary(client, run_id)
    assert generated.status_code == 200
    assert generated.json()["tape"]["source"] == "template"
    assert detail(client, run_id)["rules_config"]["bracket_tape"][FIRST]


def test_commentary_cannot_be_forged_at_creation(client):
    with respx.mock:
        mock_films()
        resp = create(client, bracket_commentary={FIRST: "Rigged!"})
    assert resp.status_code == 201
    assert "bracket_commentary" not in resp.json()["rules_config"]
    assert "bracket_tape" not in resp.json()["rules_config"]


def test_commentary_only_works_on_a_bracket_run(client):
    run_id = make_bounty_run(client).json()["id"]
    assert commentary(client, run_id).status_code == 400


# --- the AI bounty rule grammar ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"type": "runtime", "max": 80}, {"type": "runtime", "max": 80}),
        ({"type": "runtime", "min": 120, "max": 180}, {"type": "runtime", "min": 120, "max": 180}),
        ({"type": "decade", "decade": 1975}, {"type": "year", "min": 1970, "max": 1979}),
        ({"type": "year", "min": 1930, "max": 1959}, {"type": "year", "min": 1930, "max": 1959}),
        ({"type": "genre", "genre": " Musical "}, {"type": "genre", "genre": "musical"}),
        (
            {"type": "keyword", "keywords": ["Sundance", "indie", "x", 3]},
            {"type": "keyword", "keywords": ["sundance", "indie"]},
        ),
        ({"type": "keyword", "keyword": "Heist"}, {"type": "keyword", "keywords": ["heist"]}),
    ],
)
def test_valid_conditions_are_normalised(raw, expected):
    assert bounties.normalize_condition(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "runtime",
        {},
        {"type": "mood"},
        {"type": "runtime"},
        {"type": "runtime", "min": 0},
        {"type": "runtime", "min": 200, "max": 100},
        {"type": "runtime", "max": 9999},
        {"type": "runtime", "max": "long"},
        {"type": "runtime", "max": True},
        {"type": "year", "min": 1500},
        {"type": "year", "min": 2999},
        {"type": "decade", "decade": 3000},
        {"type": "genre", "genre": "gorefest"},
        {"type": "genre"},
        {"type": "keyword", "keywords": []},
        {"type": "keyword", "keywords": ["ab"]},
        {"type": "keyword", "keywords": "ab"},
    ],
)
def test_invalid_conditions_are_rejected(raw):
    assert bounties.normalize_condition(raw) is None


def test_a_rule_is_one_to_three_conditions_that_all_hold():
    assert bounties.normalize_rule({"type": "genre", "genre": "horror"}) == [
        {"type": "genre", "genre": "horror"}
    ]
    assert bounties.normalize_rule([]) is None
    assert bounties.normalize_rule([{"type": "runtime", "max": 90}] * 4) is None
    assert bounties.normalize_rule([{"type": "runtime", "max": 90}, {"type": "oops"}]) is None
    musical = bounties.custom_bounty(
        {
            "id": "ai_x",
            "title": "Golden Age Musical",
            "icon": "🎷",
            "rule": [
                {"type": "genre", "genre": "Musical"},
                {"type": "year", "min": 1930, "max": 1959},
            ],
        }
    )
    assert musical.ai is True
    base = {
        "runtime": 100,
        "popularity": 5.0,
        "language": "en",
        "countries": ["US"],
        "director_genders": [],
    }
    assert musical.check(MovieFacts(**base, year=1952, genre_ids=[10402])) is True
    assert musical.check(MovieFacts(**base, year=1975, genre_ids=[10402])) is False
    assert musical.check(MovieFacts(**base, year=1952, genre_ids=[35])) is False
    assert (
        musical.check(MovieFacts(**{**base, "runtime": None}, year=None, genre_ids=[10402]))
        is False
    )


def test_keyword_rules_search_the_text_and_missing_data_never_matches():
    sundance = bounties.custom_bounty(
        {
            "id": "ai_k",
            "title": "Indie Darling",
            "rule": [{"type": "keyword", "keywords": ["sundance", "indie"]}],
        }
    )
    base = {
        "runtime": 100,
        "year": 2005,
        "popularity": 5.0,
        "language": "en",
        "countries": ["US"],
        "director_genders": [],
    }
    assert sundance.check(MovieFacts(**base, text="an indie drama about two sisters")) is True
    assert sundance.check(MovieFacts(**base, text="a blockbuster, independently reviewed")) is False
    assert sundance.check(MovieFacts(**base)) is False
    short = bounties.custom_bounty(
        {"id": "ai_r", "title": "Quick Watch", "rule": [{"type": "runtime", "max": 80}]}
    )
    assert short.check(MovieFacts(**{**base, "runtime": 75})) is True
    assert short.check(MovieFacts(**{**base, "runtime": 0})) is False


def test_a_stored_definition_with_a_broken_rule_is_not_runnable():
    assert bounties.custom_bounty({"id": "ai_z", "title": "T", "rule": [{"type": "mood"}]}) is None
    assert (
        bounties.resolve({"custom_bounties": {"ai_z": {"id": "ai_z", "rule": "x"}}}, "ai_z") is None
    )
    assert bounties.resolve({}, "ai_missing") is None
    assert bounties.active_bounties({"active_bounties": ["short_king", "ai_missing"]}) == [
        "short_king"
    ]


GOOD = {
    "title": "Golden Age Musical",
    "icon": "🎷",
    "description": "A musical from the golden age.",
    "rule": [{"type": "genre", "genre": "Musical"}, {"type": "year", "min": 1930, "max": 1959}],
}


def test_the_models_reply_is_parsed_into_a_definition():
    bounty = bounties.parse_custom_bounty("```json\n" + json.dumps(GOOD) + "\n```")
    assert bounty["id"].startswith("ai_") and bounty["title"] == "Golden Age Musical"
    assert bounty["icon"] == "🎷" and bounty["rule"][0] == {"type": "genre", "genre": "musical"}
    thinking = bounties.parse_custom_bounty(
        "<think>hm</think>" + json.dumps({**GOOD, "icon": "x", "description": ""})
    )
    assert (
        thinking["icon"] == "✨" and thinking["description"] == "Musical genre; Released 1930-1959"
    )


@pytest.mark.parametrize(
    "reply",
    [
        "I think you should watch a musical!",
        "{not json",
        "[1, 2]",
        json.dumps({**GOOD, "title": "No"}),
        json.dumps({**GOOD, "rule": [{"type": "mood"}]}),
        json.dumps({**GOOD, "rule": []}),
        json.dumps({"title": "No rule at all"}),
    ],
)
def test_unusable_replies_are_rejected(reply):
    assert bounties.parse_custom_bounty(reply) is None


def test_a_title_already_on_the_board_is_rejected():
    assert bounties.parse_custom_bounty(json.dumps(GOOD), ["golden age musical"]) is None
    assert bounties.parse_custom_bounty(json.dumps(GOOD), ["Short King"]) is not None


# --- AI bounties in a run ---

# Every static bounty is either on the board or already done once short_king is completed.
EXHAUSTED = {
    "active_bounties": ["short_king", "time_capsule", "hidden_gem"],
    "completed_bounties": ["foreign_horizon", "female_gaze", "epic_odyssey"],
}


def exhausted_run(client, db_engine):
    run_id = make_bounty_run(client, bounty_board=True).json()["id"]
    set_board(
        db_engine,
        run_id,
        EXHAUSTED["active_bounties"],
        completed_bounties=EXHAUSTED["completed_bounties"],
    )
    return run_id


def test_when_the_static_bounties_run_out_the_ai_writes_the_replacement(client, db_engine):
    run_id = exhausted_run(client, db_engine)
    enable_llm(client)
    with respx.mock:
        mock_reply(GOOD)
        mock_film(1, runtime=80)
        step = log(client, run_id, 1)
    assert step.json()["transition_metadata"]["completed_bounty"] == "short_king"
    rules = rules_of(client, run_id)
    ai_id = step.json()["transition_metadata"]["bounty_replacement"]
    assert ai_id.startswith("ai_") and rules["active_bounties"] == [
        "time_capsule",
        "hidden_gem",
        ai_id,
    ]
    assert rules["custom_bounties"][ai_id]["title"] == "Golden Age Musical"
    assert rules["wildcards_budget"] == 1


def test_an_ai_bounty_completes_like_any_other_and_undo_restores_it(client, db_engine):
    run_id = exhausted_run(client, db_engine)
    enable_llm(client)
    with respx.mock:
        mock_reply(
            {
                "title": "Quick Watch",
                "icon": "⚡",
                "description": "Under 75 minutes.",
                "rule": [{"type": "runtime", "max": 75}],
            }
        )
        mock_film(1, runtime=80, cast=[1])
        ai_id = log(client, run_id, 1).json()["transition_metadata"]["bounty_replacement"]
        mock_film(2, runtime=70, cast=[1])
        step = log(client, run_id, 2).json()
        assert step["transition_metadata"]["completed_bounty"] == ai_id
        assert rules_of(client, run_id)["wildcards_budget"] == 2
        assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    rules = rules_of(client, run_id)
    assert ai_id in rules["active_bounties"] and rules["wildcards_budget"] == 1
    assert ai_id in rules["custom_bounties"]


def test_without_the_model_the_static_bounties_are_recycled(client, db_engine):
    run_id = exhausted_run(client, db_engine)
    with respx.mock:
        mock_film(1, runtime=80)
        step = log(client, run_id, 1)
    replacement = step.json()["transition_metadata"]["bounty_replacement"]
    assert replacement in bounties.BOUNTIES and "custom_bounties" not in rules_of(client, run_id)


def test_a_model_that_cannot_write_a_bounty_falls_back_to_the_static_ones(client, db_engine):
    run_id = exhausted_run(client, db_engine)
    enable_llm(client)
    with respx.mock:
        mock_reply("Watch something short!")
        mock_film(1, runtime=80)
        step = log(client, run_id, 1)
    assert step.status_code == 201
    assert step.json()["transition_metadata"]["bounty_replacement"] in bounties.BOUNTIES


def test_while_static_bounties_remain_the_model_is_not_asked(client, db_engine):
    run_id = make_bounty_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["short_king", "time_capsule", "epic_odyssey"])
    enable_llm(client)
    with respx.mock:
        route = mock_reply(GOOD)
        mock_film(1, runtime=80)
        step = log(client, run_id, 1)
    assert step.json()["transition_metadata"]["bounty_replacement"] in bounties.BOUNTIES
    assert route.call_count == 0


def test_rolling_a_custom_bounty_replaces_the_oldest_slot(client, db_engine):
    run_id = make_bounty_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["short_king", "time_capsule", "epic_odyssey"])
    enable_llm(client)
    with respx.mock:
        mock_reply(GOOD)
        rolled = client.post(f"/api/runs/{run_id}/bounties/custom")
        assert rolled.status_code == 200, rolled.text
        rules = rolled.json()["rules_config"]
        ai_id = rules["active_bounties"][-1]
        assert rules["active_bounties"][:2] == [
            "time_capsule",
            "epic_odyssey",
        ] and ai_id.startswith("ai_")
        assert rules["custom_bounties"][ai_id]["rule"][0] == {"type": "genre", "genre": "musical"}
        assert rules["wildcards_budget"] == 0 and rules["completed_bounties"] == []
        again = client.post(f"/api/runs/{run_id}/bounties/custom")
    assert again.status_code == 409 and "already on the board" in again.json()["detail"]


def test_rolling_a_custom_bounty_has_preconditions(client, db_engine):
    plain = make_bounty_run(client).json()["id"]
    run_id = make_bounty_run(client, bounty_board=True).json()["id"]
    assert client.post(f"/api/runs/{plain}/bounties/custom").status_code == 409  # no board
    off = client.post(f"/api/runs/{run_id}/bounties/custom")
    assert off.status_code == 409 and "model is off" in off.json()["detail"]
    enable_llm(client)
    with respx.mock:
        mock_reply("no json here")
        response = client.post(f"/api/runs/{run_id}/bounties/custom")
        assert response.status_code == 200
        assert "static quest" in response.json()["rules_config"]["bounty_roll_note"]
    assert not any(b.startswith("ai_") for b in rules_of(client, run_id)["active_bounties"])


def test_custom_bounty_definitions_cannot_be_forged(client):
    forged = {"ai_x": {"id": "ai_x", "title": "Free", "rule": [{"type": "runtime", "min": 1}]}}
    run_id = make_bounty_run(client, bounty_board=True, custom_bounties=forged).json()["id"]
    assert "custom_bounties" not in rules_of(client, run_id)
