import filecmp
import re
import shutil
from pathlib import Path

from packaging.version import Version

from common import (
    TEST_ENV,
    EVENTS_FOLDER,
    DEVEL_ENV,
    EVENTS_DIR,
)
from common.i18n import _
from common.installation_checker import InstallationChecker
from common.logger import (
    get_logger,
    input_interactive_choices,
    input_interactive_yn,
)
from common.sharly_chess_config import SharlyChessConfig
from data.loader import EventLoader
from database.sqlite.config.config_database import ConfigDatabase
from database.sqlite.event.event_database import EventDatabase
from database.sqlite.local_source_database import LocalSourceDatabaseManager
from plugins.manager import plugin_manager

logger = get_logger()


class Engine:
    def __init__(self):
        # before all the rest, initialize a SharlyChessConfig instance to set the language.
        sharly_chess_config: SharlyChessConfig = SharlyChessConfig()
        sharly_chess_config.load_and_set_env()
        logger.info(
            'Sharly Chess %s - %s - %s',
            sharly_chess_config.version,
            sharly_chess_config.copyright,
            sharly_chess_config.web_url,
        )
        logger.info('Locale: %s', sharly_chess_config.locale)
        # Engines inherited this class should stop if this flag is True.
        self.error: bool = False
        if not InstallationChecker.check():
            self.error = True
            return
        if TEST_ENV:
            # skip all the upgrade stuff on TEST_ENV (recovering tests run the migrations explicitly)
            return

        if not EventLoader().event_uniq_ids:
            logger.info(
                'No event database found, looking for old event databases in the current release...'
            )
            files: list[Path] = list(
                EVENTS_DIR.glob(f'*.{sharly_chess_config.event_database_old_ext}')
            )
            for file in files:
                event_uniq_id: str = file.stem
                logger.info('Recovering event [%s]...', event_uniq_id)
                event_database: EventDatabase = EventDatabase(event_uniq_id)
                # rename the old event database with the new extension
                file.rename(event_database.file)
                # now load the new database
                EventLoader().load_event(event_uniq_id)
        if not EventLoader().event_uniq_ids:
            logger.info(
                'Still no event database found, looking for previously installed releases of Sharly Chess...'
            )
            previous_versions: list[tuple[Version, Path]] = []
            for version_dir in Path('..').glob('*'):
                if not version_dir.is_dir():
                    logger.debug('Not a directory: [%s]', version_dir)
                    continue
                version: Version
                if matches := re.match(
                    r'^(?:papi-web|sharly-chess)-(\d+\.\d+\.\d+(?:a\d+|b\d+|rc\d+)?)(?:-windows)?$',
                    version_dir.name,
                ):
                    version: Version = Version(matches.group(1))
                else:
                    logger.debug('Not a release: [%s].', version_dir)
                    continue
                if version < Version('2.4.0'):
                    logger.debug('Version [%s] : too old, ignored.', version)
                elif version > sharly_chess_config.version:
                    logger.debug('Version [%s] : more recent, ignored.', version)
                elif version == sharly_chess_config.version:
                    logger.debug('Version [%s] : current release, ignored.', version)
                else:
                    previous_versions.append((version, version_dir))
            previous_databases: dict[tuple[Version, Path], list[Path]] = {}
            if previous_versions:
                previous_versions.sort()
                for version, version_dir in previous_versions:
                    files: list[Path] = list(
                        version_dir.glob(
                            f'{EVENTS_FOLDER}/*.{sharly_chess_config.event_database_ext}'
                        )
                    ) + list(
                        version_dir.glob(
                            f'{EVENTS_FOLDER}/*.{sharly_chess_config.event_database_old_ext}'
                        )
                    )
                    if files:
                        logger.debug(
                            '- Version [%s] (%s)',
                            version,
                            ', '.join([file.stem for file in files]),
                        )
                        previous_databases[(version, version_dir)] = files
                    else:
                        logger.debug('- Release [%s]: no events', version)
                if not previous_databases:
                    logger.debug('No events found in previously installed versions.')
            else:
                logger.info('No previously installed releases found.')
            recovered_version: Version | None = None
            if previous_databases:
                # keep the versions with databases only
                previous_versions: list[tuple[Version, Path]] = list(
                    previous_databases.keys()
                )
                previous_versions.sort()
                version_num: int | None = None
                if len(previous_databases) == 1:
                    if input_interactive_yn(
                        _(
                            'Do you want to recover the data of release [{version}]'
                        ).format(version=previous_versions[0][0]),
                        yes_is_default=True,
                    ):
                        version_num = 1
                else:
                    version_range = range(1, len(previous_versions) + 1)
                    options = {
                        str(
                            num
                        ): f'{version} ({", ".join(file.stem for file in previous_databases[(version, version_dir)])})'
                        for num, (version, version_dir) in (
                            (n, previous_versions[n - 1]) for n in version_range
                        )
                    }
                    quit_answer: str = _('Q *** THE LETTER TO ANSWER QUIT')
                    options[quit_answer] = _('Do not recover')

                    while True:
                        choice = input_interactive_choices(
                            _('Please choose the release to recover: ').format(
                                default_choice=len(previous_versions),
                                default_version=previous_versions[-1][0],
                            ),
                            options,
                            default=str(len(previous_versions)),
                        )
                        if choice is None:
                            continue
                        if choice == quit_answer:
                            break
                        if choice == '':
                            version_num = len(previous_versions)
                            break
                        try:
                            version_num = int(choice)
                            if version_num in version_range:
                                break
                            version_num = None
                        except ValueError:
                            pass
                if version_num is not None:
                    recovered_version, version_dir = previous_versions[version_num - 1]
                    self._recover_previous_version(
                        recovered_version,
                        version_dir,
                        previous_databases[(recovered_version, version_dir)],
                    )
            if DEVEL_ENV and not recovered_version:
                if input_interactive_yn(
                    _('Do you want to install example event databases'),
                    yes_is_default=True,
                ):
                    for file in SharlyChessConfig.example_events_path.glob(
                        f'*.{SharlyChessConfig.event_database_ext}'
                    ):
                        shutil.copy(file, EVENTS_DIR / file.name)

    @classmethod
    def _recover_previous_version(
        cls, version: Version, version_dir: Path, files: list[Path]
    ):
        """Recover all the data of a previous version (configuration, events, Papi files and customization files)."""
        config_database_file = (
            version_dir / EVENTS_FOLDER / ConfigDatabase.config_database_name
        )
        if config_database_file.is_file():
            from gui.server_gui_toga import SharlyChessServerToga

            logger.info('Recovering configuration from release [%s]...', version)
            # copy the configuration database to its new destination
            shutil.copy(config_database_file, ConfigDatabase().file)
            ConfigDatabase.setup()
            sharly_chess_config: SharlyChessConfig = SharlyChessConfig()
            sharly_chess_config.load_and_set_env()
            if SharlyChessServerToga.instance is not None:
                logger.debug('Applying recovered configuration to the Toga app...')
                SharlyChessServerToga.instance.update_from_sharly_chess_config()
            plugin_manager.reload_register()
        else:
            logger.debug(
                'Can not recover configuration from version [%s] (file [%s] not found).',
                version,
                config_database_file,
            )
        logger.info('Recovering events from release [%s]...', version)
        events_dir: Path = version_dir / EVENTS_FOLDER
        for file in files:
            event_uniq_id: str = file.stem
            logger.info('Recovering event [%s]...', event_uniq_id)
            event_database: EventDatabase = EventDatabase(event_uniq_id)
            # copy the event database to its new destination
            shutil.copy(file, event_database.file)
        if version < Version('3.0.0'):
            default_papi_dir = 'papi'
            previous_default_papi_path = version_dir / default_papi_dir
            default_papi_path = Path(default_papi_dir)
            default_papi_path.mkdir(parents=True, exist_ok=True)
            for file in previous_default_papi_path.glob('**/*.papi'):
                destination_file = default_papi_path / file.relative_to(
                    previous_default_papi_path
                )
                destination_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(file, destination_file)
        logger.info('Recovering misc files...')
        files_to_recover = [
            database.file
            for database in LocalSourceDatabaseManager().objects()
            if version >= database.min_recovery_version
        ]
        misc_files: list[Path] = []
        for file_to_recover in files_to_recover:
            src_file: Path = version_dir / file_to_recover
            if src_file.is_file():
                file_to_recover.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src_file, file_to_recover)
                misc_files.append(file_to_recover)
        logger.info('Recovering custom files...')
        custom_files: list[Path] = []
        custom_dir: Path = version_dir / SharlyChessConfig.custom_folder
        if custom_dir.is_dir():
            for item in custom_dir.glob('**/*'):
                if item.is_file():
                    embedded_item: Path = Path(
                        str(item).replace(
                            str(custom_dir), str(SharlyChessConfig.embedded_custom_path)
                        )
                    )
                    if not embedded_item.exists() or not filecmp.cmp(
                        item, embedded_item
                    ):
                        target_item: Path = Path(
                            str(item).replace(
                                str(custom_dir), str(SharlyChessConfig.custom_path)
                            )
                        )
                        target_item.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(item, target_item)
                        custom_files.append(item)
        logger.info('Recovering archived events...')
        archives: list[Path] = []
        archives_dir: Path = (
            version_dir / EVENTS_FOLDER / SharlyChessConfig.archives_folder
        )
        if archives_dir.is_dir():
            for item in archives_dir.glob(f'*.{SharlyChessConfig.event_archive_ext}'):
                if item.is_file():
                    target_item: Path = Path(
                        str(item).replace(
                            str(archives_dir),
                            str(SharlyChessConfig.event_archive_base_path),
                        )
                    )
                    target_item.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(item, target_item)
                    archives.append(item)
        logger.info(
            'Events recovered: %d (from directory [%s]).', len(files), events_dir
        )
        if misc_files:
            logger.info(
                'Misc files recovered: %d.',
                len(misc_files),
            )
            for misc_file in misc_files:
                logger.info('- %s', str(misc_file))
        if custom_files:
            logger.info(
                'Custom files recovered: %d (from directory [%s]).',
                len(custom_files),
                custom_dir,
            )
            for custom_file in custom_files:
                logger.info('- %s', str(custom_file).replace(str(custom_dir), ''))
        if archives:
            logger.info(
                'Archived_events recovered: %d (from directory [%s]).',
                len(archives),
                archives_dir,
            )
            for archive in archives:
                logger.info('- %s', str(archive).replace(str(archives_dir), ''))
