"""Config flow for Edulink One."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    EdulinkAuthError,
    EdulinkClient,
    EdulinkConnectionError,
    EdulinkError,
    EdulinkSchoolNotFound,
    LoginResult,
    find_school,
)
from .const import (
    CONF_ACADEMIC_YEAR_START,
    CONF_CATERING_INTERVAL,
    CONF_ESTABLISHMENT_ID,
    CONF_HOMEWORK_DAYS_AHEAD,
    CONF_HOMEWORK_INTERVAL,
    CONF_KIT_RULES,
    CONF_LEARNERS,
    CONF_LOW_BALANCE,
    CONF_SCHOOL_CODE,
    CONF_SCHOOL_NAME,
    CONF_SERVER,
    CONF_USER_ID,
    DEFAULT_ACADEMIC_YEAR_START,
    DEFAULT_CATERING_INTERVAL,
    DEFAULT_HOMEWORK_DAYS_AHEAD,
    DEFAULT_HOMEWORK_INTERVAL,
    DEFAULT_KIT_RULES,
    DEFAULT_LOW_BALANCE,
    DOMAIN,
)
from .coordinator import EdulinkConfigEntry
from .models import parse_kit_rules

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class EdulinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Edulink One."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._school: dict[str, str] = {}
        self._credentials: dict[str, str] = {}
        self._login: LoginResult | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the school code or postcode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                lookup = await find_school(session, user_input[CONF_SCHOOL_CODE])
                details = await EdulinkClient(
                    session, lookup.server, lookup.establishment_id, "", ""
                ).school_details()
            except EdulinkSchoolNotFound:
                errors["base"] = "school_not_found"
            except EdulinkConnectionError:
                errors["base"] = "cannot_connect"
            except EdulinkError:
                _LOGGER.exception("Unexpected error looking up school")
                errors["base"] = "unknown"
            else:
                if details.idp_only:
                    return self.async_abort(reason="sso_only")
                self._school = {
                    CONF_SERVER: lookup.server,
                    CONF_ESTABLISHMENT_ID: lookup.establishment_id,
                    CONF_SCHOOL_NAME: details.name,
                }
                return await self.async_step_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_SCHOOL_CODE): str}),
            errors=errors,
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Edulink username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            login, errors = await self._try_login(self._school, user_input)
            if login:
                await self.async_set_unique_id(
                    f"{self._school[CONF_ESTABLISHMENT_ID]}_{login.user_id}"
                )
                self._abort_if_unique_id_configured()
                self._credentials = user_input
                self._login = login
                if len(login.learners) > 1:
                    return await self.async_step_learners()
                return self._create([login.learners[0].id])

        return self.async_show_form(
            step_id="credentials",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, user_input
            ),
            errors=errors,
            description_placeholders={"school": self._school.get(CONF_SCHOOL_NAME, "")},
        )

    async def async_step_learners(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which children to add."""
        assert self._login is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_LEARNERS]:
                return self._create(user_input[CONF_LEARNERS])
            errors["base"] = "no_learners"

        options = [
            SelectOptionDict(value=l.id, label=f"{l.forename} {l.surname}".strip())
            for l in self._login.learners
        ]
        return self.async_show_form(
            step_id="learners",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LEARNERS, default=[o["value"] for o in options]
                    ): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
            errors=errors,
        )

    def _create(self, learner_ids: list[str]) -> ConfigFlowResult:
        assert self._login is not None
        names = [l.forename for l in self._login.learners if l.id in learner_ids]
        return self.async_create_entry(
            title=f"{self._school[CONF_SCHOOL_NAME]} ({', '.join(names)})",
            data={
                **self._school,
                **self._credentials,
                CONF_USER_ID: self._login.user_id,
                CONF_LEARNERS: learner_ids,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            login, errors = await self._try_login(entry.data, user_input)
            if login:
                if login.user_id != entry.data[CONF_USER_ID]:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA,
                user_input or {CONF_USERNAME: entry.data[CONF_USERNAME]},
            ),
            errors=errors,
        )

    async def _try_login(
        self, school: Mapping[str, Any], credentials: Mapping[str, Any]
    ) -> tuple[LoginResult | None, dict[str, str]]:
        client = EdulinkClient(
            async_get_clientsession(self.hass),
            school[CONF_SERVER],
            school[CONF_ESTABLISHMENT_ID],
            credentials[CONF_USERNAME],
            credentials[CONF_PASSWORD],
        )
        try:
            login = await client.login()
        except EdulinkAuthError:
            return None, {"base": "invalid_auth"}
        except EdulinkConnectionError:
            return None, {"base": "cannot_connect"}
        except EdulinkError:
            _LOGGER.exception("Unexpected error logging in")
            return None, {"base": "unknown"}
        # Validation only: do not leave a session open.
        await client.logout()
        if not login.learners or not login.learners[0].id:
            return None, {"base": "no_learners"}
        return login, {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: EdulinkConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return EdulinkOptionsFlow()


class EdulinkOptionsFlow(OptionsFlowWithReload):
    """Polling intervals, thresholds, kit reminders and the academic year."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_kit_rules(user_input.get(CONF_KIT_RULES, ""))
            except ValueError:
                errors[CONF_KIT_RULES] = "invalid_kit_rules"
            else:
                return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOW_BALANCE, default=DEFAULT_LOW_BALANCE
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=0.5,
                        unit_of_measurement="£",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_HOMEWORK_INTERVAL, default=DEFAULT_HOMEWORK_INTERVAL
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=240,
                        step=5,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_HOMEWORK_DAYS_AHEAD, default=DEFAULT_HOMEWORK_DAYS_AHEAD
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=365,
                        step=1,
                        unit_of_measurement="days",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_KIT_RULES, default=DEFAULT_KIT_RULES): TextSelector(
                    TextSelectorConfig(multiline=True)
                ),
                vol.Required(
                    CONF_CATERING_INTERVAL, default=DEFAULT_CATERING_INTERVAL
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=240,
                        step=5,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_ACADEMIC_YEAR_START, default=DEFAULT_ACADEMIC_YEAR_START
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=12, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.config_entry.options
            ),
            errors=errors,
        )
