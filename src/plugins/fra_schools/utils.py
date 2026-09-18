import re
from dataclasses import dataclass, asdict
from functools import partial, cached_property
from logging import Logger
from typing import Self, Any, TYPE_CHECKING
from collections import Counter
from collections.abc import Collection

from common import SharlyChessException
from common.i18n.utils import normalized_key
from common.logger import get_logger
from data.event import Player, Event
from database.sqlite.event.event_database import EventDatabase
from plugins.fra_schools import PLUGIN_NAME
from plugins.utils import PluginUtils, PluginData
from web.controllers.base_controller import WebContext

if TYPE_CHECKING:
    from data.input_output import TournamentImporter
    from data.tournament import Tournament
    from plugins.fra_schools.fra_schools_database import FRASchoolsDatabase

get_data = partial(PluginUtils.get_plugin_data, PLUGIN_NAME)
logger: Logger = get_logger()


@dataclass
class FRASchool(PluginData):
    id: int = 0
    code: str | None = None
    name: str = ''
    postal_code: str | None = None
    department: str | None = None
    city: str | None = None

    @classmethod
    def from_stored_value(cls, stored_value: dict[str, Any], id_: int = 0) -> Self:
        return cls(
            id=id_,
            code=stored_value.get('code'),
            name=stored_value.get('name', ''),
            department=stored_value.get('department'),
            postal_code=stored_value.get('postal_code'),
            city=stored_value.get('city'),
        )

    def to_stored_value(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_form_data(
        cls,
        data: dict[str, str],
        previous_object: Self | None = None,
        action: str | None = None,
        id_: int = 0,
    ) -> Self:
        return cls(
            id=id_,
            code=WebContext.form_data_to_str(data, 'fra_school_code'),
            name=WebContext.form_data_to_str(data, 'fra_school_name') or '',
            postal_code=WebContext.form_data_to_str(data, 'fra_school_postal_code'),
            city=WebContext.form_data_to_str(data, 'fra_school_city'),
        )

    def to_form_data(self, action: str | None = None) -> dict[str, str]:
        return WebContext.values_dict_to_form_data(
            {
                'fra_school_code': self.code,
                'fra_school_name': self.name,
                'fra_school_postal_code': self.postal_code,
                'fra_school_city': self.city,
            }
        )

    @classmethod
    def from_source_row(cls, row: dict[str, Any]) -> Self:
        return cls(
            code=row['code'],
            name=row['name'],
            department=row['department'],
            postal_code=row['postal_code'],
            city=row['city'],
        )

    @property
    def full_name(self) -> str:
        full_name = self.label
        if self.code:
            full_name = f'{self.code} {full_name}'
        return full_name

    @property
    def short_name(self) -> str:
        if self.postal_code:
            return f'{self.postal_code} - {self.name}'
        return self.name

    @property
    def label(self) -> str:
        label = self.name
        if self.city:
            label += f', {self.city}'
        if self.postal_code:
            label += f' ({self.postal_code})'
        return label

    LABEL_PATTERN = re.compile(
        r'^(?P<name>.*), (?P<city>[^,]*) \((?P<postal_code>.{5})\)$'
    )

    @classmethod
    def from_label(cls, label: str) -> Self:
        """Parse a string formatted in the full name format into FRASchool object."""
        match = cls.LABEL_PATTERN.fullmatch(label)
        if match is None:
            raise SharlyChessException(f'Invalid school label format: {label}')
        return cls(
            name=match.group('name'),
            city=match.group('city'),
            postal_code=match.group('postal_code'),
        )

    @cached_property
    def tooltip(self) -> str:
        if not self.code or not self.city:
            return ''
        tooltip = ''
        if self.code:
            tooltip += f'<div class="text-center fw-bold">{self.code}</div>'
        tooltip += f'<div class="text-center">{self.name}</div>'
        if self.city:
            city = self.city
            if self.postal_code:
                city += f' ({self.postal_code})'
            tooltip += f'<div class="text-center fst-italic">{city}</div>'
        return tooltip

    @property
    def sort_key(self) -> tuple:
        return (
            self.postal_code or '',
            normalized_key(self.city),
            normalized_key(self.name),
            self.code or '',
        )

    def __lt__(self, other: 'FRASchool') -> bool:
        if not isinstance(other, FRASchool):
            return NotImplemented
        return self.sort_key > other.sort_key

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FRASchool):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass
class FRASchoolsEventPluginData(PluginData):
    fra_schools_by_id: dict[int, FRASchool]
    hide_school_code_on_upload: bool

    @property
    def fra_schools(self) -> Collection[FRASchool]:
        return self.fra_schools_by_id.values()

    @classmethod
    def from_stored_value(cls, stored_value: dict[str, Any]) -> Self:
        return cls(
            fra_schools_by_id={
                int(school_id): FRASchool.from_stored_value(
                    school_dict, id_=int(school_id)
                )
                for school_id, school_dict in stored_value.get(
                    'fra_schools_by_id', {}
                ).items()
            },
            hide_school_code_on_upload=stored_value.get(
                'hide_school_code_on_upload', False
            ),
        )

    def to_stored_value(self) -> dict[str, Any]:
        return {
            'fra_schools_by_id': {
                str(school_id): school.to_stored_value()
                for school_id, school in self.fra_schools_by_id.items()
            },
            'hide_school_code_on_upload': self.hide_school_code_on_upload,
        }

    @classmethod
    def from_form_data(
        cls,
        data: dict[str, str],
        previous_object: Self | None = None,
        action: str | None = None,
    ) -> Self:
        return cls(
            fra_schools_by_id=(
                previous_object.fra_schools_by_id if previous_object else {}
            ),
            hide_school_code_on_upload=WebContext.form_data_to_bool(
                data, 'hide_school_code_on_upload'
            ),
        )

    def to_form_data(self, action: str | None = None) -> dict[str, str]:
        return WebContext.values_dict_to_form_data(
            {
                'hide_school_code_on_upload': self.hide_school_code_on_upload,
            }
        )


@dataclass
class FRASchoolsPlayerPluginData(PluginData):
    fra_school_id: int | None

    @classmethod
    def from_stored_value(cls, stored_value: dict[str, Any]) -> Self:
        return cls(
            fra_school_id=stored_value.get('fra_school_id'),
        )

    def to_stored_value(self) -> dict[str, Any]:
        return {
            'fra_school_id': self.fra_school_id,
        }

    @classmethod
    def from_form_data(
        cls,
        data: dict[str, str],
        previous_object: Self | None = None,
        action: str | None = None,
    ) -> Self:
        return cls(
            fra_school_id=WebContext.form_data_to_int(data, 'fra_school_id'),
        )

    def to_form_data(self, action: str | None = None) -> dict[str, str]:
        return WebContext.values_dict_to_form_data(
            {
                'fra_school_id': self.fra_school_id,
            }
        )


class FRASchoolsImportLookup:
    """Reuse one FRA schools DB connection and cache lookups during an import."""

    _ATTR = '_fra_schools_import_lookup'

    def __init__(self):
        self._school_id_by_code: dict[str, int | None] = {}
        self._db: 'FRASchoolsDatabase | None' = None
        self._db_available: bool | None = None

    @classmethod
    def for_importer(cls, importer: 'TournamentImporter') -> Self:
        lookup = getattr(importer, cls._ATTR, None)
        if lookup is None:
            lookup = cls()
            setattr(importer, cls._ATTR, lookup)
            importer.post_import_task.append(lookup.close_task)
        return lookup

    def close_task(self, _tournament: 'Tournament') -> None:
        self.close()

    def close(self):
        if self._db is not None:
            self._db.__exit__(None, None, None)
            self._db = None

    def _ensure_db(self) -> 'FRASchoolsDatabase | None':
        if self._db_available is False:
            return None
        if self._db is not None:
            return self._db
        from plugins.fra_schools.fra_schools_database import FRASchoolsDatabase

        if not FRASchoolsDatabase.file_path().exists():
            self._db_available = False
            return None
        self._db = FRASchoolsDatabase()
        self._db.__enter__()
        self._db_available = True
        return self._db

    def resolve_school_id(
        self,
        event: Event,
        importer: 'TournamentImporter',
        school_code: str,
        *,
        last_name: str,
        first_name: str,
    ) -> int | None:
        if school_code in self._school_id_by_code:
            return self._school_id_by_code[school_code]

        plugin_data = FRASchoolsUtils.get_event_plugin_data(event)
        school_id = next(
            (s.id for s in plugin_data.fra_schools if s.code == school_code),
            None,
        )
        if school_id:
            self._school_id_by_code[school_code] = school_id
            return school_id

        db = self._ensure_db()
        if db is None:
            self._school_id_by_code[school_code] = None
            return None

        school = db.get_school_by_code(school_code)
        if not school:
            logger.warning(
                'Player [%s %s] - No school found for code [%s] (ignored).',
                last_name,
                first_name,
                school_code,
            )
            self._school_id_by_code[school_code] = None
            return None

        importer.stored_event_modified = True
        school_id = FRASchoolsUtils.add_event_school(event, school, save=False)
        self._school_id_by_code[school_code] = school_id
        return school_id


class FRASchoolsUtils:
    @staticmethod
    def get_event_plugin_data(event: Event) -> FRASchoolsEventPluginData:
        plugin_data = event.plugin_data[PLUGIN_NAME]
        assert isinstance(plugin_data, FRASchoolsEventPluginData)
        return plugin_data

    @classmethod
    def get_event_school_counts(cls, players: Collection[Player]) -> Counter[int]:
        school_counts: Counter[int] = Counter[int]()
        for player in players:
            school_counts[
                FRASchoolsUtils.get_player_plugin_data(player).fra_school_id or 0
            ] += 1
        return school_counts

    @staticmethod
    def get_player_plugin_data(player: Player) -> FRASchoolsPlayerPluginData:
        plugin_data = player.plugin_data[PLUGIN_NAME]
        assert isinstance(plugin_data, FRASchoolsPlayerPluginData)
        return plugin_data

    @classmethod
    def get_player_school(cls, player: Player) -> FRASchool | None:
        player_school_id = cls.get_player_plugin_data(player).fra_school_id
        if not player_school_id:
            return None
        event_schools_by_id = cls.get_event_plugin_data(player.event).fra_schools_by_id
        return event_schools_by_id.get(player_school_id, None)

    @classmethod
    def get_school_by_id(cls, event: Event, school_id: int) -> FRASchool | None:
        event_schools_by_id = cls.get_event_plugin_data(event).fra_schools_by_id
        return event_schools_by_id.get(school_id, None)

    @classmethod
    def get_school_by_code(cls, event: Event, code: str) -> FRASchool | None:
        event_schools_by_id = cls.get_event_plugin_data(event).fra_schools_by_id
        return next(
            (school for school in event_schools_by_id.values() if school.code == code),
            None,
        )

    @classmethod
    def add_event_school(
        cls,
        event: Event,
        school: FRASchool,
        update_existing: bool = False,
        save: bool = True,
        database: EventDatabase | None = None,
    ) -> int:
        """Add a school to the event, returning its ID.
        If a school already exists with the same code:
            - if *update_existing* it is updated
            - otherwise it is ignored."""
        plugin_data = FRASchoolsUtils.get_event_plugin_data(event)
        school_id: int | None = None
        if school.code:
            school_id = next(
                (s.id for s in plugin_data.fra_schools if s.code == school.code),
                None,
            )
        if not school_id:
            school_id = (
                max(plugin_data.fra_schools_by_id | {0: ''}) + 1
                if plugin_data.fra_schools_by_id
                else 1
            )
        elif not update_existing:
            return school_id
        school.id = school_id
        plugin_data.fra_schools_by_id[school_id] = school
        event.stored_event.plugin_data[PLUGIN_NAME] = plugin_data.to_stored_value()
        if save:
            if database:
                database.update_stored_event(event.stored_event)
            else:
                with EventDatabase(event.uniq_id, True) as database:
                    database.update_stored_event(event.stored_event)
        return school_id

    @classmethod
    def update_event_school(cls, event: Event, school: FRASchool) -> None:
        plugin_data = FRASchoolsUtils.get_event_plugin_data(event)
        plugin_data.fra_schools_by_id[school.id] = school
        stored_event = event.stored_event
        stored_event.plugin_data[PLUGIN_NAME] = plugin_data.to_stored_value()

        with EventDatabase(event.uniq_id, True) as database:
            database.update_stored_event(stored_event)

    @classmethod
    def delete_event_school(cls, event: Event, fra_school_id: int) -> None:
        plugin_data = FRASchoolsUtils.get_event_plugin_data(event)
        if fra_school_id in plugin_data.fra_schools_by_id:
            del plugin_data.fra_schools_by_id[fra_school_id]
        stored_event = event.stored_event
        stored_event.plugin_data[PLUGIN_NAME] = plugin_data.to_stored_value()
        with EventDatabase(event.uniq_id, True) as database:
            database.update_stored_event(stored_event)

    @staticmethod
    def extract_school_code(school_str: str) -> str | None:
        if re.match(r'^\d{7}[A-Z]', school_str):
            return school_str[:8]
        return None
