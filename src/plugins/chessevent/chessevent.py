from types import ModuleType
from typing import Any, TYPE_CHECKING

from packaging.version import Version

from common.i18n import _
from data.input_output import TournamentImporter
from data.tournament import Tournament
from database.sqlite.event.event_database import EventDatabase
from database.sqlite.event.event_store import (
    StoredEvent,
    StoredTournament,
    StoredPlayer,
)
from plugins.chessevent import migrations, PLUGIN_NAME
from plugins.chessevent.ticketchess_controller import TicketchessController
from plugins.chessevent.tournament_importer.data import ChessEventPlayer
from plugins.chessevent.utils import (
    ChessEventConfigPluginData,
    ChessEventEventPluginData,
    ChessEventTournamentPluginData,
    ChessEventUtils,
)
from plugins.ffe.ffe import FfePlugin
from plugins.hookspec import hookimpl, hookspec
from plugins.migration import PluginMigrationManager
from plugins.utils import Plugin, PluginData
from web.controllers.base_controller import WebContext, BaseController

if TYPE_CHECKING:
    from data.event import Event


class ChessEventPluginHooks:
    @hookspec
    def augment_stored_player_from_chessevent_player(
        self,
        event: 'Event',
        importer: TournamentImporter,
        stored_player: StoredPlayer,
        chessevent_player: ChessEventPlayer,
    ):
        """Augment player data when fetched from ChessEvent / Ticketchess."""


class ChessEventPlugin(Plugin[ChessEventConfigPluginData]):
    data_class = ChessEventConfigPluginData

    @staticmethod
    def static_id() -> str:
        return PLUGIN_NAME

    @staticmethod
    def static_name() -> str:
        return _('ChessEvent')

    @property
    def dependencies(self) -> list[type[Plugin]]:
        return [FfePlugin]

    @property
    def description(self) -> str:
        return _(
            'Import events and registrations from Ticketchess '
            '(ChessEvent-compatible API).'
        )

    @property
    def version(self) -> Version:
        return Version('0.1.0')

    @property
    def federation(self) -> str | None:
        return 'FRA'

    @property
    def base_migration_module(self) -> ModuleType:
        return migrations

    @property
    def hookspecs(self) -> type | None:
        return ChessEventPluginHooks

    @property
    def event_form_fields_template(self) -> str:
        return '/chessevent_event_form_fields.html'

    def used_by_stored_tournament(
        self, stored_event: StoredEvent, stored_tournament: StoredTournament
    ) -> bool:
        ce_data = stored_tournament.plugin_data.get(PLUGIN_NAME, {})
        return ce_data.get('tournament_name', None) is not None

    # ---------------------------------------------------------------------------------
    # Initialisation and configuration
    # ---------------------------------------------------------------------------------

    @hookimpl
    def get_event_migration_manager(
        self, event_database: EventDatabase
    ) -> PluginMigrationManager:
        return self.get_migration_manager(event_database)

    @property
    def controllers(self) -> list[type[BaseController]]:
        return [TicketchessController]

    # ---------------------------------------------------------------------------------
    # Events
    # ---------------------------------------------------------------------------------

    @hookimpl
    def on_event_duplicated(self, event_database: EventDatabase):
        stored_event = event_database.load_stored_event()

        stored_event.plugin_data[PLUGIN_NAME] = (
            ChessEventEventPluginData().to_stored_value()
        )
        event_database.update_stored_event(stored_event)

        for stored_tournament in stored_event.stored_tournaments:
            new_plugin_data = ChessEventTournamentPluginData()
            stored_tournament.plugin_data[PLUGIN_NAME] = (
                new_plugin_data.to_stored_value()
            )
            event_database.update_stored_tournament(stored_tournament)

    @hookimpl
    def get_event_plugin_data_class(self) -> tuple[str, type[PluginData]]:
        return self.id, ChessEventEventPluginData

    @hookimpl
    def validate_event_form_fields(
        self,
        action: str,
        event: 'Event | None',
        data: dict[str, str],
        errors: dict[str, str],
    ):
        federation = WebContext.form_data_to_str(data, 'federation')
        if federation != 'FRA':
            return

        chessevent_server_url = WebContext.form_data_to_str(
            data, field := 'chessevent_server_url'
        )
        if chessevent_server_url and not chessevent_server_url.startswith(
            ('http://', 'https://')
        ):
            errors[field] = _(
                'Please enter a valid ChessEvent server URL (http:// or https://).'
            )

    # ---------------------------------------------------------------------------------
    # Tournaments
    # ---------------------------------------------------------------------------------

    @hookimpl
    def get_tournament_plugin_data_class(self) -> tuple[str, type[PluginData]]:
        return self.id, ChessEventTournamentPluginData

    @hookimpl
    def get_tournament_page_template_context(self) -> dict[str, Any]:
        return {'chessevent_utils': ChessEventUtils}

    @hookimpl
    def get_tournament_card_connexion_template(
        self, tournament: Tournament
    ) -> str | None:
        if not ChessEventUtils.resolve_tournament_name(tournament):
            return None
        return '/chessevent_tournament_card_connexion.html'
