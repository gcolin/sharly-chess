from dataclasses import dataclass, field
from logging import Logger
from urllib.parse import urlparse, urlunparse

from requests import Session, Response
from requests.exceptions import RequestException

from common import SharlyChessException
from common.i18n import _
from common.logger import get_logger
from plugins.chessevent.chessevent_status import (
    AuthErrorChessEventStatus,
    UnauthorizedErrorChessEventStatus,
    TournamentNotFoundChessEventStatus,
    EventNotFoundChessEventStatus,
    ConnectionErrorChessEventStatus,
)
from plugins.chessevent.exceptions import ChessEventStatusError

logger: Logger = get_logger()


@dataclass
class ChessEventTournamentRequestData:
    event_id: str
    tournament_name: str = ''
    bearer_token: str | None = field(default=None, repr=False)


class ChessEventSession(Session):
    """A Requests session specialised for Ticketchess /chessevent (JWT Bearer)."""

    def __init__(self, download_url: str):
        super().__init__()
        self.download_url = download_url.rstrip('/')

    @property
    def tournaments_url(self) -> str:
        """URL of the tournaments list endpoint."""
        if self.download_url.endswith('/download'):
            return self.download_url[: -len('/download')] + '/tournaments'
        return f'{self.download_url}/tournaments'

    @staticmethod
    def api_base_url(server_url: str) -> str:
        """Normalize a ChessEvent/Ticketchess API base URL (…/chessevent)."""
        base = server_url.strip().rstrip('/')
        if base.endswith('/download'):
            base = base[: -len('/download')]
        return base

    def list_tournaments(
        self,
        event_id: str,
        bearer_token: str | None = None,
    ) -> list[str]:
        """Lists tournament names for a ChessEvent event_id."""
        post = {'event_id': event_id}
        logger.debug('Listing tournaments from ChessEvent (%s)...', event_id)
        data = self._post_json(self.tournaments_url, post, bearer_token, event_id)
        tournaments = data.get('tournaments') if isinstance(data, dict) else data
        if not isinstance(tournaments, list):
            raise SharlyChessException(
                f'Unexpected tournaments response: {data!r}'
            )
        return [str(name) for name in tournaments]

    def read_tournament_data(
        self, request_data: ChessEventTournamentRequestData
    ) -> str:
        """Reads the data of a ChessEvent tournament."""
        event_id = request_data.event_id
        tournament_name = request_data.tournament_name
        post = {
            'event_id': event_id,
            'tournament_name': tournament_name,
        }
        logger.debug(
            'Reading data from the ChessEvent platform (%s)...',
            f'{event_id}/[{tournament_name}]',
        )
        return self._post_raw(
            self.download_url, post, request_data.bearer_token, event_id
        )

    def _post_json(
        self,
        url: str,
        post: dict[str, str],
        bearer_token: str | None,
        event_id: str,
    ) -> dict | list:
        raw = self._post_raw(url, post, bearer_token, event_id)
        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise SharlyChessException(
                f'Invalid JSON from ChessEvent [{url}]: {error}'
            ) from error

    def _post_raw(
        self,
        url: str,
        post: dict[str, str],
        bearer_token: str | None,
        event_id: str,
    ) -> str:
        headers = {}
        if bearer_token:
            headers['Authorization'] = f'Bearer {bearer_token}'
        try:
            response: Response = self.post(
                url, data=post, headers=headers, allow_redirects=False
            )
            while response.status_code in [301, 302]:
                redirect_url = response.headers['location']
                logger.debug('Redirection to  %s...', redirect_url)
                response = self.post(
                    redirect_url, data=post, headers=headers, allow_redirects=False
                )
        except RequestException as ex:
            logger.error('Failed to read [%s]: %s.', url, ex)
            raise ChessEventStatusError(
                _('Connection to the ChessEvent server failed.'),
                ConnectionErrorChessEventStatus(),
            )
        data: str = response.content.decode()
        if response.status_code == 200:
            return data
        logger.error(
            'ChessEvent request failed with status %d.',
            response.status_code,
        )
        match response.status_code:
            case 401 | 497:
                raise ChessEventStatusError(
                    _(
                        'Authentication failed. '
                        'Please check your credentials and try again.'
                    ),
                    AuthErrorChessEventStatus(),
                )
            case 403:
                raise ChessEventStatusError(
                    _(
                        'The event [{event_id}] is not accessible with this token.'
                    ).format(event_id=event_id),
                    UnauthorizedErrorChessEventStatus(),
                )
            case 496:
                raise SharlyChessException('Missing parameter.')
            case 498:
                tournament_name = post.get('tournament_name', '')
                raise ChessEventStatusError(
                    _(
                        'Tournament [{tournament_name}] does not'
                        ' exist in event [{event_id}].'
                    ).format(
                        tournament_name=tournament_name,
                        event_id=event_id,
                    ),
                    TournamentNotFoundChessEventStatus(),
                )
            case 499:
                raise ChessEventStatusError(
                    _('Event [{event_id}] does not exist.').format(event_id=event_id),
                    EventNotFoundChessEventStatus(),
                )
            case _:
                raise SharlyChessException(
                    f'Unknown response code: [{response.status_code}].'
                )


def ticketchess_chessevent_url(ticketchess_base_url: str) -> str:
    """Build the ChessEvent API base URL from a Ticketchess origin."""
    base = ticketchess_base_url.strip().rstrip('/')
    parsed = urlparse(base)
    path = (parsed.path or '').rstrip('/')
    if path.endswith('/chessevent'):
        return urlunparse(parsed._replace(path=path, params='', query='', fragment=''))
    new_path = f'{path}/chessevent' if path else '/chessevent'
    return urlunparse(
        parsed._replace(path=new_path, params='', query='', fragment='')
    )
