from __future__ import annotations

from typing import TYPE_CHECKING, Collection, Iterable

from data.screens.manager import ScreenTypeManager
from database.sqlite.event.event_store import StoredScreen
from utils import Utils

if TYPE_CHECKING:
    from data.event import Event
    from data.tournament import Tournament
    from database.sqlite.event.event_database import EventDatabase


# Simple screens expected for a newly created tournament.
SIMPLE_TOURNAMENT_SCREEN_TYPE_IDS: frozenset[str] = frozenset({'input', 'players'})


def create_tournament_screens(
    database: EventDatabase,
    event: Event,
    tournament: Tournament,
    type_ids: Collection[str] | None = None,
    *,
    exclude_type_ids: Iterable[str] = (),
) -> list[str]:
    """Create set-based screens for a tournament.

    When ``type_ids`` is None, all set-based screen types supported by the
    event are created. Otherwise only the given type ids are considered.
    Returns the static ids of the screens that were created.
    """
    assert tournament.id is not None
    excluded = set(exclude_type_ids)
    timer_id: int | None = None
    if len(event.timers_by_id) == 1:
        timer_id = next(iter(event.timers_by_id.keys()))

    created: list[str] = []
    for screen_type in ScreenTypeManager(event).objects():
        if not screen_type.has_screen_sets:
            continue
        if not screen_type.supports_event_type(event.event_type):
            continue
        if type_ids is not None and screen_type.value not in type_ids:
            continue
        if screen_type.value in excluded:
            continue
        type_fields = screen_type.create_form_data(event)
        columns = type_fields.pop('columns', 1)
        stored_screen: StoredScreen = database.add_stored_screen(
            StoredScreen(
                id=None,
                uniq_id=event.get_unused_screen_uniq_id(
                    base_uniq_id=Utils.name_to_uniq_id(
                        f'{tournament.name}-{screen_type.value}'
                    )
                ),
                type=screen_type.value,
                public=True,
                name=f'{screen_type.name} ({tournament.name})',
                columns=columns,
                font_size=None,
                menu_text=None,
                timer_id=timer_id,
                message_default=True,
                message_text=None,
                **type_fields,
            )
        )
        assert stored_screen.id is not None
        database.add_stored_screen_set(stored_screen.id, tournament.id)
        created.append(screen_type.value)
    return created
