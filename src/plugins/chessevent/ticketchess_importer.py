"""Import a full Ticketchess event/collection into Sharly Chess via JWT."""

import time
from logging import Logger

from common.exception import ImporterError, SharlyChessException
from common.i18n import _, ngettext
from common.logger import get_logger
from common.sharly_chess_config import SharlyChessConfig
from data.event import Event
from data.loader import EventLoader
from data.screens.defaults import (
    SIMPLE_TOURNAMENT_SCREEN_TYPE_IDS,
    create_tournament_screens,
)
from database.sqlite.config.config_database import ConfigDatabase
from database.sqlite.event.event_database import EventDatabase
from database.sqlite.event.event_store import StoredEvent
from plugins.chessevent import PLUGIN_NAME
from plugins.chessevent.chessevent_session import (
    ChessEventSession,
    ChessEventTournamentRequestData,
    ticketchess_chessevent_url,
)
from plugins.chessevent.exceptions import ChessEventStatusError
from plugins.chessevent.tournament_importer.importer import ChessEventTournamentImporter
from plugins.chessevent.tournament_importer.options import (
    ChessEventEventOption,
    ChessEventTournamentOption,
)
from plugins.chessevent.utils import (
    ChessEventConfigPluginData,
    ChessEventEventPluginData,
    DEFAULT_TICKETCHESS_BASE_URL,
)
from plugins.ffe import PLUGIN_NAME as FFE_PLUGIN_NAME
from plugins.fra_schools import PLUGIN_NAME as FRA_SCHOOLS_PLUGIN_NAME
from plugins.manager import plugin_manager
from data.input_output.tournament_importer_options import TournamentImporterOption
from utils import Utils
from utils.enum import PlayerRatingType

logger: Logger = get_logger()


class _JwtChessEventTournamentImporter(ChessEventTournamentImporter):
    """ChessEvent importer authenticated with a Sharly import JWT."""

    def __init__(self, event_id: str, tournament_name: str, jwt: str):
        super().__init__(
            [
                ChessEventEventOption(event_id),
                ChessEventTournamentOption(tournament_name),
            ]
        )
        self._jwt = jwt
        self._event_id = event_id
        self._tournament_name = tournament_name

    @staticmethod
    def available_options() -> list[type[TournamentImporterOption]]:
        return [
            ChessEventEventOption,
            ChessEventTournamentOption,
        ]

    def validate_options(self, event: Event | None = None):
        return

    def _resolve_request_data(self, event: Event) -> ChessEventTournamentRequestData:
        return ChessEventTournamentRequestData(
            event_id=self._event_id,
            tournament_name=self._tournament_name,
            bearer_token=self._jwt,
        )


class TicketchessEventImporter:
    """Creates a Sharly event and imports all Ticketchess tournaments."""

    def __init__(
        self,
        jwt: str,
        event_id: str,
        name: str,
        ticketchess_base_url: str | None = None,
    ):
        self.jwt = jwt
        self.event_id = event_id
        self.name = name.strip() or event_id
        self.ticketchess_base_url = (
            ticketchess_base_url or DEFAULT_TICKETCHESS_BASE_URL
        ).rstrip('/')
        self.chessevent_server_url = ticketchess_chessevent_url(
            self.ticketchess_base_url
        )

    def import_event(self) -> tuple[str, int, list[str]]:
        """Returns (uniq_id, imported_count, error_messages)."""
        import_started = time.perf_counter()
        download_url = f'{self.chessevent_server_url.rstrip("/")}/download'
        logger.info(
            'Ticketchess import start event_id=[%s] server=[%s]',
            self.event_id,
            self.chessevent_server_url,
        )
        session = ChessEventSession(download_url)
        list_started = time.perf_counter()
        tournament_names = session.list_tournaments(
            event_id=self.event_id,
            bearer_token=self.jwt,
        )
        logger.info(
            'Ticketchess list_tournaments done in %.0f ms count=%d',
            (time.perf_counter() - list_started) * 1000,
            len(tournament_names),
        )
        if not tournament_names:
            raise SharlyChessException(
                _('No tournament found for Ticketchess event [{event_id}].').format(
                    event_id=self.event_id
                )
            )

        create_started = time.perf_counter()
        uniq_id = self._create_event()
        logger.info(
            'Ticketchess create_event done in %.0f ms uniq_id=[%s]',
            (time.perf_counter() - create_started) * 1000,
            uniq_id,
        )
        imported = 0
        errors: list[str] = []
        for index, tournament_name in enumerate(tournament_names, start=1):
            tournament_started = time.perf_counter()
            logger.info(
                'Ticketchess tournament %d/%d start name=[%s]',
                index,
                len(tournament_names),
                tournament_name,
            )
            try:
                reload_started = time.perf_counter()
                event = EventLoader().load_event(uniq_id)
                logger.info(
                    'Ticketchess pre-import EventLoader.load_event in %.0f ms',
                    (time.perf_counter() - reload_started) * 1000,
                )
                importer = _JwtChessEventTournamentImporter(
                    self.event_id, tournament_name, self.jwt
                )
                tournament_id = importer.load_tournament(event)
                event = EventLoader().load_event(uniq_id)
                tournament = event.tournaments_by_id[tournament_id]
                with EventDatabase(uniq_id, write=True) as database:
                    create_tournament_screens(
                        database,
                        event,
                        tournament,
                        SIMPLE_TOURNAMENT_SCREEN_TYPE_IDS,
                    )
                imported += 1
                logger.info(
                    'Ticketchess tournament %d/%d done in %.0f ms name=[%s]',
                    index,
                    len(tournament_names),
                    (time.perf_counter() - tournament_started) * 1000,
                    tournament_name,
                )
            except (ImporterError, ChessEventStatusError, SharlyChessException) as error:
                logger.error(
                    'Failed to import Ticketchess tournament [%s] after %.0f ms: %s',
                    tournament_name,
                    (time.perf_counter() - tournament_started) * 1000,
                    error,
                )
                errors.append(f'{tournament_name}: {error}')
        logger.info(
            'Ticketchess import finished in %.0f ms imported=%d errors=%d uniq_id=[%s]',
            (time.perf_counter() - import_started) * 1000,
            imported,
            len(errors),
            uniq_id,
        )
        return uniq_id, imported, errors

    def _create_event(self) -> str:
        config = SharlyChessConfig()
        uniq_id = EventLoader().get_unused_event_uniq_id(
            Utils.name_to_uniq_id(self.name)
        )
        plugin_ids: list[str] = []
        for plugin_id in (PLUGIN_NAME, FFE_PLUGIN_NAME, FRA_SCHOOLS_PLUGIN_NAME):
            plugin = plugin_manager.plugins_by_id.get(plugin_id)
            if plugin:
                plugin_ids.append(plugin_id)

        plugin_data: dict[str, dict] = {}
        for (
            plugin_id,
            plugin_data_class,
        ) in Event.plugin_data_class_by_plugin_id().items():
            if plugin_id == PLUGIN_NAME:
                plugin_data[plugin_id] = ChessEventEventPluginData(
                    event_id=self.event_id,
                    server_url=self.chessevent_server_url,
                ).to_stored_value()
            else:
                plugin_data[plugin_id] = plugin_data_class.from_stored_value(
                    {}
                ).to_stored_value()

        stored_event = StoredEvent(
            uniq_id=uniq_id,
            name=self.name,
            federation='FRA',
            public=False,
            player_rating_type=PlayerRatingType.FIDE.value,
            plugin_data=plugin_data,
            enabled_plugins=plugin_ids,
            timer_colors=config.default_timer_colors,  # type: ignore
            timer_delays=config.default_timer_delays,  # type: ignore
            background_color=config.default_background_color,
            message_background_color=config.default_message_background_color,
            message_color=config.default_message_color,
        )
        EventDatabase(uniq_id).create()
        with EventDatabase(uniq_id, write=True) as database:
            database.update_stored_event(stored_event)
        return uniq_id


def ensure_chessevent_plugin_enabled():
    """Enable ChessEvent (and dependencies) so Ticketchess import hooks/routes work."""
    from copy import copy

    changed = False
    with ConfigDatabase(write=True) as database:
        for plugin_id in (PLUGIN_NAME, FFE_PLUGIN_NAME, FRA_SCHOOLS_PLUGIN_NAME):
            plugin = plugin_manager.plugins_by_id.get(plugin_id)
            if not plugin or plugin.is_enabled:
                continue
            stored_plugin = copy(plugin.context.stored_plugin)
            stored_plugin.is_enabled = True
            database.update_stored_plugin(stored_plugin)
            changed = True
    if changed:
        plugin_manager.reload_register()


def get_ticketchess_base_url() -> str:
    plugin = plugin_manager.plugins_by_id.get(PLUGIN_NAME)
    if not plugin:
        return DEFAULT_TICKETCHESS_BASE_URL
    try:
        data: ChessEventConfigPluginData = plugin.get_plugin_data()
        return data.resolved_ticketchess_base_url
    except Exception:
        return DEFAULT_TICKETCHESS_BASE_URL


def save_ticketchess_base_url(url: str):
    plugin = plugin_manager.plugins_by_id[PLUGIN_NAME]
    data = ChessEventConfigPluginData(ticketchess_base_url=url.rstrip('/'))
    with ConfigDatabase(write=True) as config_database:
        stored_plugin = plugin.context.stored_plugin
        stored_plugin.plugin_data = data.to_stored_value()
        config_database.update_stored_plugin(stored_plugin)
    plugin.reload_context()


def format_import_summary(imported: int, errors: list[str]) -> str:
    parts: list[str] = []
    if imported:
        parts.append(
            ngettext(
                '{count} tournament imported',
                '{count} tournaments imported',
                imported,
            ).format(count=imported)
        )
    if errors:
        parts.append(
            ngettext(
                '{count} tournament failed',
                '{count} tournaments failed',
                len(errors),
            ).format(count=len(errors))
        )
    return '. '.join(parts) + '.'
