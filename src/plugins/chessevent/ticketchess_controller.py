from urllib.parse import urlencode
from typing import Annotated

from litestar import get, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect, Template
from litestar_htmx import HTMXRequest, HTMXTemplate, ClientRedirect

from common.exception import SharlyChessException
from common.i18n import _
from common.logger import get_logger
from common.sharly_chess_config import SharlyChessConfig
from data.access_levels.actions import AuthAction
from plugins.chessevent.exceptions import ChessEventStatusError
from plugins.chessevent.ticketchess_importer import (
    TicketchessEventImporter,
    ensure_chessevent_plugin_enabled,
    format_import_summary,
    get_ticketchess_base_url,
    save_ticketchess_base_url,
)
from web.controllers.admin.base_admin_controller import AdminWebContext, BaseAdminController
from web.controllers.base_controller import WebContext
from web.guards import ActionGuard
from web.messages import Message
from web.urls import admin_event_tournaments_url

logger = get_logger()


class TicketchessController(BaseAdminController):
    """Routes for importing Ticketchess events into Sharly Chess."""

    @staticmethod
    def _render_start_modal(
        web_context: AdminWebContext,
        ticketchess_base_url: str,
        errors: dict[str, str] | None = None,
    ) -> HTMXTemplate:
        return HTMXTemplate(
            template_name='/ticketchess_start_modal.html',
            context=web_context.template_context
            | {
                'ticketchess_base_url': ticketchess_base_url,
                'admin_tab': web_context.admin_tab or 'home',
                'errors': errors or {},
            },
            re_target='#modal-wrapper',
            re_swap='innerHTML',
            trigger_event='modal_opened',
            after='settle',
        )

    @get(
        path='/ticketchess/start-modal',
        name='ticketchess-start-modal',
        guards=[ActionGuard(AuthAction.MANAGE_EVENTS)],
    )
    async def ticketchess_start_modal(
        self,
        request: HTMXRequest,
        admin_tab: str | None = None,
    ) -> Template:
        web_context = AdminWebContext(request, admin_tab=admin_tab)
        return self._render_start_modal(web_context, get_ticketchess_base_url())

    @post(
        path='/ticketchess/start',
        name='ticketchess-start',
        guards=[ActionGuard(AuthAction.MANAGE_EVENTS)],
    )
    async def ticketchess_start(
        self,
        request: HTMXRequest,
        data: Annotated[
            dict[str, str],
            Body(media_type=RequestEncodingType.URL_ENCODED),
        ],
    ) -> ClientRedirect | Template:
        admin_tab = data.get('admin_tab') or 'home'
        web_context = AdminWebContext(request, admin_tab=admin_tab)
        base_url = (
            WebContext.form_data_to_str(data, 'ticketchess_base_url') or ''
        ).strip()
        if not base_url.startswith(('http://', 'https://')):
            return self._render_start_modal(
                web_context,
                base_url or get_ticketchess_base_url(),
                errors={
                    'ticketchess_base_url': _(
                        'Please enter a valid Ticketchess URL (http:// or https://).'
                    )
                },
            )
        save_ticketchess_base_url(base_url)
        ensure_chessevent_plugin_enabled()
        callback = f'{SharlyChessConfig().local_url.rstrip("/")}/ticketchess/callback'
        select_url = (
            f'{base_url.rstrip("/")}/sharly/select?{urlencode({"callback": callback})}'
        )
        return ClientRedirect(select_url)

    @get(
        path='/ticketchess/callback',
        name='ticketchess-callback',
        guards=[ActionGuard(AuthAction.MANAGE_EVENTS)],
    )
    async def ticketchess_callback(
        self,
        request: HTMXRequest,
        jwt: str | None = None,
        event_id: str | None = None,
        name: str | None = None,
    ) -> Redirect:
        if not jwt or not event_id:
            Message.error(
                request,
                _('Ticketchess import failed: missing token or event id.'),
            )
            return Redirect('/')

        ensure_chessevent_plugin_enabled()
        try:
            importer = TicketchessEventImporter(
                jwt=jwt,
                event_id=event_id,
                name=name or event_id,
                ticketchess_base_url=get_ticketchess_base_url(),
            )
            uniq_id, imported, errors = importer.import_event()
        except (ChessEventStatusError, SharlyChessException) as error:
            logger.error('Ticketchess import failed: %s', error)
            Message.error(
                request,
                _('Ticketchess import failed: {error}').format(error=error),
            )
            return Redirect('/')
        except Exception as error:
            logger.exception('Unexpected Ticketchess import error')
            Message.error(
                request,
                _('Ticketchess import failed: {error}').format(error=error),
            )
            return Redirect('/')

        summary = format_import_summary(imported, errors)
        if errors and not imported:
            Message.error(request, summary)
        elif errors:
            Message.warning(
                request,
                _('Event [{uniq_id}] created. {summary}').format(
                    uniq_id=uniq_id, summary=summary
                ),
            )
        else:
            Message.success(
                request,
                _('Event [{uniq_id}] imported from Ticketchess. {summary}').format(
                    uniq_id=uniq_id, summary=summary
                ),
            )
        return Redirect(admin_event_tournaments_url(request, uniq_id))
