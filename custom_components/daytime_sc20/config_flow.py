"""Config and options flows for the daytime SC20.

The device has no authentication of any kind, so setting it up needs only an address. The
flow still connects before accepting one, because a reachable HTTP server is not proof that
the WebSocket works.

The options flow is the integration's configuration frontend, laid out to mirror the
controller's own web app so the settings sit where a user of the vendor interface expects
them.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
)

from .api import (
    Acclimate,
    Cloud,
    DaycycleDescription,
    Moon,
    SC20Client,
    SC20ConnectionError,
    SC20Error,
    SC20ValidationError,
    async_check_connection,
)
from .backup import async_backup_daycycle
from .const import (
    CONF_ACTIVE,
    CONF_BRIGHTNESS,
    CONF_CHANNEL_LEVELS,
    CONF_COLOR,
    CONF_DURATION_DAYS,
    CONF_END,
    CONF_INDIVIDUAL,
    CONF_LUNAR_CYCLE,
    CONF_MAX_DURATION,
    CONF_MAX_LEVEL,
    CONF_MAX_PER_DAY,
    CONF_MIN_DURATION,
    CONF_MIN_LEVEL,
    CONF_PAUSED,
    CONF_PROBABILITY,
    CONF_REDUCTION,
    CONF_SCAN_INTERVAL,
    CONF_START,
    CONF_SUNRISE,
    CONF_SUNSET,
    DAY_MINUTES_MAX,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_ACCLIMATE_DAYS,
    MAX_CLOUD_DURATION,
    MAX_CLOUDS_PER_DAY,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MOON_COLORS,
)
from .coordinator import SC20Coordinator

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


async def _async_probe(hass: Any, host: str) -> tuple[str, str]:
    """Connect and read enough to identify the device.

    Returns the unique id (the master's BSSID) and a display name. Raises
    `SC20ConnectionError` if the device is not reachable or is not an SC20.
    """
    session = async_get_clientsession(hass)

    if not await async_check_connection(host, session):
        raise SC20ConnectionError(f"no SC20 web server answered at {host}")

    client = SC20Client(host, session)
    try:
        await client.connect()
        state = await client.async_refresh()
    except SC20Error as err:
        raise SC20ConnectionError(str(err)) from err
    finally:
        await client.disconnect()

    if state.device_info is None:
        raise SC20ConnectionError(f"{host} did not identify itself as an SC20")

    unique_id = state.mesh.master if state.mesh and state.mesh.master else host
    name = state.device_info.aquarium_name or state.device_info.name or "daytime SC20"
    return unique_id, name


class SC20ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle adding a controller."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                unique_id, name = await _async_probe(self.hass, host)
            except SC20ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(title=name, data={CONF_HOST: host})

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> SC20OptionsFlow:
        return SC20OptionsFlow()


class SC20OptionsFlow(OptionsFlow):
    """The integration's configuration screens.

    Laid out to mirror the controller's own web app, so that someone who has used the
    vendor interface finds the same settings in the same groupings: a daycycle page with
    start/end and sunrise/sunset durations, then a page each for moonlight, clouds and
    acclimatisation.

    Every page except Connection writes straight to the device rather than to Home
    Assistant's options — these are the lamp's settings, not ours, and the vendor app is
    free to change them behind our back. Each page therefore reads the current values from
    the device when it opens.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "daycycle",
                "moonlight",
                "clouds",
                "acclimatisation",
                "connection",
            ],
        )

    # --- plumbing ------------------------------------------------------------------------

    @property
    def _coordinator(self) -> SC20Coordinator | None:
        return getattr(self.config_entry, "runtime_data", None)

    def _finish(self) -> ConfigFlowResult:
        """Close the dialog without disturbing the stored options."""
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def _async_write(self, action) -> str | None:
        """Run a device write, returning an error key if it did not land."""
        coordinator = self._coordinator
        if coordinator is None:
            return "not_loaded"
        try:
            await coordinator.async_write_then_refresh(action)
        except SC20ValidationError:
            # Raised before anything was sent — the caller reports the specific problem.
            raise
        except SC20Error as err:
            _LOGGER.error("could not write settings to %s: %s", coordinator.client.host, err)
            return "cannot_connect"
        return None

    # --- daycycle ------------------------------------------------------------------------

    async def async_step_daycycle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The vendor app's easy-mode page: day window, ramp lengths, brightness.

        Writing here replaces the whole lighting programme, so the existing one is backed
        up first. Someone who has hand-edited an expert curve would lose it, which is why
        the form says so rather than silently flattening it into a trapezoid.
        """
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        errors: dict[str, str] = {}
        current = coordinator.client.state.description or DaycycleDescription()

        if user_input is not None:
            try:
                described = _description_from_input(user_input, current)
                daycycle = described.to_daycycle()
            except SC20ValidationError as err:
                errors["base"] = "invalid_daycycle"
                _LOGGER.debug("rejected daycycle from options flow: %s", err)
            else:
                await async_backup_daycycle(self.hass, coordinator.client)
                client = coordinator.client
                error = await self._async_write(
                    lambda: client.async_set_daycycle(daycycle, described)
                )
                if error:
                    errors["base"] = error
                else:
                    return self._finish()

        return self.async_show_form(
            step_id="daycycle",
            data_schema=_daycycle_schema(current),
            errors=errors,
            description_placeholders={
                "expert_warning": (
                    "This device is running a hand-edited expert curve. Saving here"
                    " replaces it with a simple sunrise/sunset shape."
                )
                if current.expert_mode
                else ""
            },
        )

    # --- effects -------------------------------------------------------------------------

    async def async_step_moonlight(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        current = coordinator.client.state.moon
        if current is None:
            return self.async_abort(reason="no_data")

        errors: dict[str, str] = {}
        if user_input is not None:
            updated = Moon(
                active=user_input[CONF_ACTIVE],
                cycle=user_input[CONF_LUNAR_CYCLE],
                minimum=int(user_input[CONF_MIN_LEVEL]),
                maximum=int(user_input[CONF_MAX_LEVEL]),
                # At least one colour must carry the moonlight, or it emits nothing.
                color="".join(user_input[CONF_COLOR]) or current.color or "b",
                start=_time_to_minute(user_input[CONF_START]),
                end=_time_to_minute(user_input[CONF_END]),
            )
            client = coordinator.client
            error = await self._async_write(lambda: client.async_set_moon(updated))
            if error:
                errors["base"] = error
            else:
                return self._finish()

        return self.async_show_form(
            step_id="moonlight", data_schema=_moon_schema(current), errors=errors
        )

    async def async_step_clouds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        current = coordinator.client.state.cloud
        if current is None:
            return self.async_abort(reason="no_data")

        errors: dict[str, str] = {}
        if user_input is not None:
            minimum = int(user_input[CONF_MIN_LEVEL])
            maximum = int(user_input[CONF_MAX_LEVEL])
            min_duration = int(user_input[CONF_MIN_DURATION])
            max_duration = int(user_input[CONF_MAX_DURATION])
            if minimum > maximum:
                errors[CONF_MIN_LEVEL] = "min_above_max"
            elif min_duration > max_duration:
                errors[CONF_MIN_DURATION] = "min_above_max"
            else:
                updated = Cloud(
                    active=user_input[CONF_ACTIVE],
                    max_per_day=int(user_input[CONF_MAX_PER_DAY]),
                    probability=int(user_input[CONF_PROBABILITY]),
                    min_intensity=minimum,
                    max_intensity=maximum,
                    min_duration_minutes=min_duration,
                    max_duration_minutes=max_duration,
                    mode=current.mode,
                )
                client = coordinator.client
                error = await self._async_write(lambda: client.async_set_cloud(updated))
                if error:
                    errors["base"] = error
                else:
                    return self._finish()

        return self.async_show_form(
            step_id="clouds", data_schema=_cloud_schema(current), errors=errors
        )

    async def async_step_acclimatisation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        current = coordinator.client.state.acclimate
        if current is None:
            return self.async_abort(reason="no_data")

        errors: dict[str, str] = {}
        if user_input is not None:
            updated = Acclimate(
                active=user_input[CONF_ACTIVE],
                paused=user_input[CONF_PAUSED],
                duration_days=int(user_input[CONF_DURATION_DAYS]),
                intensity_reduction=int(user_input[CONF_REDUCTION]),
                # Maintained by the device; resetting it would restart the ramp.
                current_day=current.current_day,
            )
            client = coordinator.client
            error = await self._async_write(lambda: client.async_set_acclimate(updated))
            if error:
                errors["base"] = error
            else:
                return self._finish()

        return self.async_show_form(
            step_id="acclimatisation",
            data_schema=_acclimate_schema(current),
            errors=errors,
            description_placeholders={"current_day": str(current.current_day)},
        )

    # --- connection ----------------------------------------------------------------------

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The only page that stores anything in Home Assistant rather than on the lamp."""
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                )
            }
        )
        return self.async_show_form(step_id="connection", data_schema=schema)


# --- form building -------------------------------------------------------------------------
#
# The vendor app expresses times of day as clock times and ramp lengths as durations, so
# these forms do too: a time selector for "when", a minutes box for "how long".


def _minute_to_time(minute: int) -> str:
    """Minutes since midnight to the "HH:MM:SS" a time selector expects."""
    minute = max(0, min(DAY_MINUTES_MAX, int(minute)))
    return f"{minute // 60:02d}:{minute % 60:02d}:00"


def _time_to_minute(value: Any) -> int:
    """Whatever a time selector returned, back to minutes since midnight."""
    if isinstance(value, str):
        parts = value.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    # Selectors may hand back a time object rather than a string.
    return int(getattr(value, "hour", 0)) * 60 + int(getattr(value, "minute", 0))


def _percent(default: float, *, step: int = 1) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0, max=100, step=step, mode=NumberSelectorMode.SLIDER, unit_of_measurement="%"
        )
    )


def _minutes(maximum: int, *, step: int = 1) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0,
            max=maximum,
            step=step,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="min",
        )
    )


def _daycycle_schema(current: DaycycleDescription) -> vol.Schema:
    """The easy-mode page. `end` is a clock time, so it stops one minute short of 1440."""
    levels = current.levels
    return vol.Schema(
        {
            vol.Required(CONF_START, default=_minute_to_time(current.start)): TimeSelector(),
            vol.Required(
                CONF_END, default=_minute_to_time(min(current.end, DAY_MINUTES_MAX))
            ): TimeSelector(),
            vol.Required(CONF_SUNRISE, default=current.sunrise): _minutes(DAY_MINUTES_MAX),
            vol.Required(CONF_SUNSET, default=current.sunset): _minutes(DAY_MINUTES_MAX),
            vol.Required(CONF_BRIGHTNESS, default=current.intensity): _percent(
                current.intensity
            ),
            vol.Required(CONF_INDIVIDUAL, default=current.individual): BooleanSelector(),
            **{
                vol.Optional(key, default=levels[index]): _percent(levels[index])
                for index, key in enumerate(CONF_CHANNEL_LEVELS)
            },
        }
    )


def _description_from_input(
    user_input: dict[str, Any], current: DaycycleDescription
) -> DaycycleDescription:
    """Turn easy-mode form values back into the sidecar the device stores.

    `expert_mode` is cleared: whatever curve was there, what is being saved now is a plain
    sunrise/sunset shape, and claiming otherwise would mislead the vendor app.
    """
    return DaycycleDescription(
        conf_id=current.conf_id,
        expert_mode=False,
        start=_time_to_minute(user_input[CONF_START]),
        end=_time_to_minute(user_input[CONF_END]),
        sunrise=int(user_input[CONF_SUNRISE]),
        sunset=int(user_input[CONF_SUNSET]),
        intensity=int(user_input[CONF_BRIGHTNESS]),
        individual=bool(user_input[CONF_INDIVIDUAL]),
        intensities=tuple(
            int(user_input.get(key, user_input[CONF_BRIGHTNESS])) for key in CONF_CHANNEL_LEVELS
        ),
    )


def _moon_schema(current: Moon) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ACTIVE, default=current.active): BooleanSelector(),
            vol.Required(CONF_LUNAR_CYCLE, default=current.cycle): BooleanSelector(),
            vol.Required(CONF_MIN_LEVEL, default=current.minimum): _percent(current.minimum),
            vol.Required(CONF_MAX_LEVEL, default=current.maximum): _percent(current.maximum),
            vol.Required(
                CONF_COLOR, default=[c for c in MOON_COLORS if c in current.color]
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(MOON_COLORS),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key="moon_color",
                )
            ),
            vol.Required(CONF_START, default=_minute_to_time(current.start)): TimeSelector(),
            vol.Required(CONF_END, default=_minute_to_time(current.end)): TimeSelector(),
        }
    )


def _cloud_schema(current: Cloud) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ACTIVE, default=current.active): BooleanSelector(),
            vol.Required(CONF_MAX_PER_DAY, default=current.max_per_day): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=MAX_CLOUDS_PER_DAY, step=10, mode=NumberSelectorMode.SLIDER
                )
            ),
            vol.Required(CONF_MIN_DURATION, default=current.min_duration_minutes): _minutes(
                MAX_CLOUD_DURATION
            ),
            vol.Required(CONF_MAX_DURATION, default=current.max_duration_minutes): _minutes(
                MAX_CLOUD_DURATION
            ),
            vol.Required(CONF_MIN_LEVEL, default=current.min_intensity): _percent(
                current.min_intensity, step=5
            ),
            vol.Required(CONF_MAX_LEVEL, default=current.max_intensity): _percent(
                current.max_intensity, step=5
            ),
            vol.Required(CONF_PROBABILITY, default=current.probability): _percent(
                current.probability
            ),
        }
    )


def _acclimate_schema(current: Acclimate) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ACTIVE, default=current.active): BooleanSelector(),
            vol.Required(CONF_PAUSED, default=current.paused): BooleanSelector(),
            vol.Required(CONF_DURATION_DAYS, default=current.duration_days): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_ACCLIMATE_DAYS,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="d",
                )
            ),
            vol.Required(CONF_REDUCTION, default=current.intensity_reduction): _percent(
                current.intensity_reduction
            ),
        }
    )
