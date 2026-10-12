"""Cache-grounded link facts shared by the engine hook and pitch renderer."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlmodel import Session, col, select

from app.config import get_settings
from app.facets import semantic, tropes
from app.models.cache import (
    CachedActor,
    CachedCrewCredit,
    CachedGenre,
    CachedMovie,
    CachedMovieCast,
    CachedMovieDirector,
)
from app.models.run import RunStep
from app.schemas.discovery import DiscoveryCandidate, DiscoveryConnection
from app.schemas.link import LinkContext, LinkFilm
from app.services.cache_repo import parse_release_year
from app.utils.countries import parse_countries

if TYPE_CHECKING:
    from app.engines.base import BaseChallengeEngine


def _film(session: Session, movie: CachedMovie) -> LinkFilm:
    genres = [session.get(CachedGenre, genre_id) for genre_id in movie.genre_ids or []]
    vibe = semantic.cached_projection(movie).get("vibe_quadrant")
    return LinkFilm(
        movie_id=movie.tmdb_id,
        title=movie.title,
        year=parse_release_year(movie.release_date),
        genres=[
            genre.name if genre else f"Genre {genre_id}"
            for genre_id, genre in zip(movie.genre_ids or [], genres, strict=True)
        ],
        vibe=vibe.title() if isinstance(vibe, str) else None,
        plot=movie.overview,
    )


def _people(
    session: Session,
    previous: CachedMovie,
    candidate: CachedMovie,
    policy: str,
    cast_limit: int | None,
) -> list[DiscoveryConnection]:
    result = []
    cast_rows = []
    director_rows = []
    if policy in {"shared_cast", "shared_any_person"}:
        rows = session.exec(
            select(CachedMovieCast).where(
                col(CachedMovieCast.movie_id).in_([previous.tmdb_id, candidate.tmdb_id])
            )
        ).all()
        cast_rows = rows
        left = {
            row.actor_id: row
            for row in rows
            if row.movie_id == previous.tmdb_id
            and (cast_limit is None or row.cast_order is not None and row.cast_order < cast_limit)
        }
        for row in rows:
            if row.movie_id != candidate.tmdb_id or row.actor_id not in left:
                continue
            if cast_limit is not None and (row.cast_order is None or row.cast_order >= cast_limit):
                continue
            actor = session.get(CachedActor, row.actor_id)
            if actor:
                result.append(
                    DiscoveryConnection(
                        actor_id=actor.tmdb_id,
                        actor_name=actor.name,
                        character_in_frontier=left[row.actor_id].character_name,
                        character_in_candidate=row.character_name,
                    )
                )
    if policy in {"shared_director", "shared_any_person"}:
        rows = session.exec(
            select(CachedMovieDirector).where(
                col(CachedMovieDirector.movie_id).in_([previous.tmdb_id, candidate.tmdb_id])
            )
        ).all()
        director_rows = rows
        left_ids = {row.person_id for row in rows if row.movie_id == previous.tmdb_id}
        result.extend(
            DiscoveryConnection(kind="director", actor_id=row.person_id, actor_name=row.name)
            for row in rows
            if row.movie_id == candidate.tmdb_id and row.person_id in left_ids
        )
    if policy == "shared_any_person":
        rows = session.exec(
            select(CachedCrewCredit).where(
                col(CachedCrewCredit.movie_id).in_([previous.tmdb_id, candidate.tmdb_id])
            )
        ).all()
        roles: dict[int, dict[int, list[tuple[str, str]]]] = {
            previous.tmdb_id: {},
            candidate.tmdb_id: {},
        }
        for row in cast_rows:
            if cast_limit is not None and (row.cast_order is None or row.cast_order >= cast_limit):
                continue
            actor = session.get(CachedActor, row.actor_id)
            if actor is not None:
                roles[row.movie_id].setdefault(row.actor_id, []).append((actor.name, "Actor"))
        for row in director_rows:
            roles[row.movie_id].setdefault(row.person_id, []).append((row.name, "Director"))
        for row in rows:
            roles[row.movie_id].setdefault(row.person_id, []).append((row.person_name, row.job))
        for person_id in roles[previous.tmdb_id].keys() & roles[candidate.tmdb_id].keys():
            for name, left_role in roles[previous.tmdb_id][person_id]:
                for _, right_role in roles[candidate.tmdb_id][person_id]:
                    if left_role != right_role or left_role not in {"Actor", "Director"}:
                        result.append(
                            DiscoveryConnection(
                                kind="craft",
                                actor_id=person_id,
                                actor_name=name,
                                role_in_frontier=left_role,
                                role_in_candidate=right_role,
                            )
                        )
    return result


def describe(
    engine: "BaseChallengeEngine",
    session: Session,
    previous: CachedMovie,
    candidate: CachedMovie,
    meta: DiscoveryCandidate,
    rules: dict,
    history: Sequence[RunStep],
) -> LinkContext:
    from app.engines.algorithms import SemanticTropeEngine
    from app.engines.grid_crawler import GridCrawlerEngine
    from app.engines.rabbit_hole import compliance, tier_for_depth
    from app.engines.traversal import get_policy

    mode = engine.game_type
    context = LinkContext(
        mode=mode,
        mode_name=engine.display_name,
        mode_goal=engine.rulebook.goal,
        previous=_film(session, previous),
        candidate=_film(session, candidate),
    )
    policy = (
        rules.get("tug_traversal", "shared_cast")
        if mode == "tug_of_war"
        else rules.get("link", "shared_cast")
        if mode in {"connect_canon", "canon_infiltration"}
        else "shared_director"
        if mode == "auteur_relay"
        else "shared_any_person"
        if mode == "crew_craft"
        else "shared_cast"
    )
    graph_modes = {
        "cinechain",
        "auteur_relay",
        "crew_craft",
        "meet_in_middle",
        "meet_in_the_middle",
        "rabbit_hole",
        "tug_of_war",
        "connect_canon",
        "canon_infiltration",
        "canon_island",
    }
    if mode in graph_modes or rules.get("require_cast_link"):
        context.shared_people = _people(
            session,
            previous,
            candidate,
            policy,
            rules.get("max_cast_order") or get_settings().pathfinder_cast_limit,
        )
        if context.shared_people:
            names = list(dict.fromkeys(person.actor_name for person in context.shared_people))
            context.bridge_facts.append(f"{get_policy(policy).label.lower()}: {', '.join(names)}")
            roles = [
                f"{person.actor_name}: {person.character_in_frontier or person.role_in_frontier}"
                f" to {person.character_in_candidate or person.role_in_candidate}"
                for person in context.shared_people
                if (person.character_in_frontier or person.role_in_frontier)
                and (person.character_in_candidate or person.role_in_candidate)
            ]
            if roles:
                context.bridge_facts.append("roles evolve: " + "; ".join(roles))
        elif get_policy(policy).graph:
            context.unknowns.append("Shared people are not verified in the cache")
    if context.previous.year is not None and context.candidate.year is not None:
        delta = context.candidate.year - context.previous.year
        context.bridge_facts.append(
            f"release-year jump {delta:+d} ({context.previous.year} to {context.candidate.year})"
        )
    if context.previous.genres and context.candidate.genres:
        context.bridge_facts.append(
            f"genre pivot from {' / '.join(context.previous.genres)} to {' / '.join(context.candidate.genres)}"
        )
    if context.previous.vibe and context.candidate.vibe:
        context.bridge_facts.append(
            f"tone shifts from {context.previous.vibe} to {context.candidate.vibe}"
        )

    if isinstance(engine, SemanticTropeEngine):
        evidence = tropes.evidence_for(session, [previous.tmdb_id, candidate.tmdb_id])
        shared = sorted(
            set(evidence.get(previous.tmdb_id, {})) & set(evidence.get(candidate.tmdb_id, {}))
        )
        context.shared_tropes = [slug.replace("-", " ").title() for slug in shared]
        metric = engine.measure(previous, candidate)
        if metric is not None:
            context.similarity_percent = round(metric * 100)
            context.rule_payoffs.append(f"{context.similarity_percent}% plot similarity")
        if shared:
            context.rule_payoffs.append(f"shared themes: {', '.join(context.shared_tropes)}")
        if metric is None and not shared:
            context.unknowns.append("Plot similarity and shared themes are unverified")
    elif mode == "tug_of_war":
        engine.annotate_candidates([meta], rules, history)
        effect = {
            "home": "Build",
            "invasion": "Raid",
            "neutral": "Bank",
            "sudden_neutral": "Bank",
        }.get(meta.tug_effect)
        if effect is not None:
            from app.engines.tug_of_war import _territory, plane_for_rules

            plane = plane_for_rules(rules)
            territory = (
                meta.tug_territory
                if rules.get("tug_rules_version") == 4
                else _territory(
                    meta.release_year,
                    meta.origin_country,
                    rules,
                )
            )
            pole = (
                plane.pole_a.label
                if territory == "team_a"
                else plane.pole_b.label
                if territory == "team_b"
                else "neutral band"
            )
            context.rule_payoffs.append(f"{effect}: {meta.tug_points} points; target pole {pole}")
            if effect == "Bank":
                context.rule_payoffs.append("bank a multiplier for the next scoring pull")
            if meta.tug_breaks_streak:
                context.rule_payoffs.append("breaks the opposing build streak")
        else:
            context.unknowns.append("Tug territory and pull effect are unverified")
        context.rule_payoffs.append(f"traversal: {get_policy(policy).label}")
    elif mode == "rabbit_hole":
        tier = tier_for_depth(len(history), rules)
        verdict = compliance(session, tier, candidate)
        context.rule_payoffs.append(
            f"Tier {tier.number} ({tier.rule}): "
            + (
                "satisfies the active rule"
                if verdict is True
                else "does not satisfy the active rule"
                if verdict is False
                else "rule is unverified"
            )
        )
    elif mode == "chrono_climb":
        context.rule_payoffs.append(f"release years must {rules.get('direction', 'climb')}")
    elif mode == "world_passport":
        context.rule_payoffs.append(
            f"country crossing: {', '.join(parse_countries(previous.origin_country)) or 'unknown'}"
            f" to {', '.join(parse_countries(candidate.origin_country)) or 'unknown'}"
        )
    elif mode == "genre_pendulum":
        from app.engines.genre_pendulum import pendulum_state

        state = pendulum_state(rules, len(history))
        context.rule_payoffs.append(
            f"pendulum target: {state.target}; next swing: {state.next_target}"
        )
        common = set(previous.genre_ids or []) & set(candidate.genre_ids or [])
        context.rule_payoffs.append(
            "shared genre bridge: "
            + (
                ", ".join(
                    row.name if (row := session.get(CachedGenre, item)) else str(item)
                    for item in sorted(common)
                )
                or "not verified"
            )
        )
    elif mode == "historical_time_travel":
        context.rule_payoffs.append(
            f"story setting leap: {previous.narrative_era_label or 'unknown'}"
            f" ({previous.narrative_year if previous.narrative_year is not None else 'unknown'})"
            f" to {candidate.narrative_era_label or 'unknown'}"
            f" ({candidate.narrative_year if candidate.narrative_year is not None else 'unknown'})"
        )
    elif isinstance(engine, GridCrawlerEngine):
        cells = [
            cell["id"]
            for cell in engine._claimable_cells(rules, history)
            if engine._matches_cell(candidate.tmdb_id, cell)
        ]
        context.rule_payoffs.append(
            f"adjacent grid cells available to claim: {', '.join(cells) or 'none verified'}"
        )
    if mode in {"connect_canon", "canon_infiltration"}:
        if mode == "canon_infiltration" and rules.get("fog"):
            context.unknowns.append("Target distances are hidden by Fog")
            return context
        from app.services import goal_graph

        if mode == "connect_canon":
            legs = rules.get("legs") or []
            leg = int(rules.get("current_leg") or 0)
            targets = [int(legs[leg]["to"])] if leg < len(legs) else []
            if targets and (target := session.get(CachedMovie, targets[0])) is not None:
                context.rule_payoffs.append(f"target waypoint: {target.title}")
        else:
            from app.engines.canon_infiltration import CanonInfiltrationEngine
            from app.models.curated import CuratedList

            if (
                rules.get("target_list_id")
                and (target_list := session.get(CuratedList, rules["target_list_id"])) is not None
            ):
                context.rule_payoffs.append(f"target canon: {target_list.title}")

            targets = (
                engine._target_ids(rules) if isinstance(engine, CanonInfiltrationEngine) else []
            )
        distances = (
            goal_graph.distance_to_targets(
                session,
                [previous.tmdb_id, candidate.tmdb_id],
                targets,
                policy=policy,
                max_depth=6,
                max_seconds=0.5,
            )
            if targets
            else {}
        )
        distance = distances.get(candidate.tmdb_id)
        before = distances.get(previous.tmdb_id)
        context.rule_payoffs.append(
            f"cached target distance: {distance} hops"
            if distance is not None
            else "target distance is unknown within the cache search budget"
        )
        if distance is not None and before is not None:
            context.rule_payoffs.append(f"shaves {before - distance} hops toward the target")
    return context
