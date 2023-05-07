"""ADT Pulse HA binary sensor integration.

This adds a sensor for ADT Pulse alarm systems so that all the ADT
motion sensors and switches automatically appear in Home Assistant. This
automatically discovers the ADT sensors configured within Pulse and
exposes them into HA.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from pyadtpulse import PyADTPulse
from pyadtpulse.const import STATE_OK
from pyadtpulse.site import ADTPulseSite
from pyadtpulse.zones import ADTPulseZoneData

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ADTPULSE_DATA_ATTRIBUTION, ADTPULSE_DOMAIN, LOG
from .coordinator import ADTPulseDataUpdateCoordinator

# please keep these alphabetized to make changes easier
ADT_DEVICE_CLASS_TAG_MAP = {
    "co": BinarySensorDeviceClass.CO,
    "doorWindow": BinarySensorDeviceClass.DOOR,
    "flood": BinarySensorDeviceClass.MOISTURE,
    "garage": BinarySensorDeviceClass.GARAGE_DOOR,  # FIXME: need ADT type
    "fire": BinarySensorDeviceClass.HEAT,
    "motion": BinarySensorDeviceClass.MOTION,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "glass": BinarySensorDeviceClass.TAMPER,
}

ADT_SENSOR_ICON_MAP = {
    BinarySensorDeviceClass.CO: ("mdi:molecule-co", "mdi:checkbox-marked-circle"),
    BinarySensorDeviceClass.DOOR: ("mdi:door-open", "mdi:door"),
    BinarySensorDeviceClass.GARAGE_DOOR: (
        "mdi:garage-open-variant",
        "mdi:garage-variant",
    ),
    BinarySensorDeviceClass.HEAT: ("mdi:fire", "mdi:smoke-detector-variant"),
    BinarySensorDeviceClass.MOISTURE: ("mdi:home-flood", "mdi:heat-wave"),
    BinarySensorDeviceClass.MOTION: ("mdi:run-fast", "mdi:motion-sensor"),
    BinarySensorDeviceClass.SMOKE: ("mdi:fire", "mdi:smoke-detector-variant"),
    BinarySensorDeviceClass.TAMPER: ("mdi:window-open", "mdi:window-closed"),
    BinarySensorDeviceClass.WINDOW: (
        "mdi:window-open-variant",
        "mdi:window-closed-variant",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors for an ADT Pulse installation."""
    coordinator: ADTPulseDataUpdateCoordinator = hass.data[ADTPULSE_DOMAIN][
        entry.entry_id
    ]
    adt_service = coordinator.adtpulse
    if not adt_service:
        LOG.error("ADT Pulse service not initialized, cannot create sensors")
        return

    if not adt_service.sites:
        LOG.error(f"ADT's Pulse service returned NO sites: {adt_service}")
        return

    for site in adt_service.sites:
        if not isinstance(site, ADTPulseSite):
            raise RuntimeError("pyadtpulse returned invalid site object type")
        if not site.zones_as_dict:
            LOG.error(
                "ADT's Pulse service returned NO zones (sensors) for site: "
                f"{adt_service.sites} ... {adt_service}"
            )
            continue
        entities = [
            ADTPulseZoneSensor(coordinator, site, zone_id)
            for zone_id in site.zones_as_dict.keys()
        ]
        async_add_entities(entities)
        async_add_entities([ADTPulseGatewaySensor(coordinator, adt_service)])


class ADTPulseZoneSensor(
    CoordinatorEntity[ADTPulseDataUpdateCoordinator], BinarySensorEntity
):
    """HASS zone binary sensor implementation for ADT Pulse."""

    # zone = {'id': 'sensor-12', 'name': 'South Office Motion',
    # 'tags': ['sensor', 'motion'], 'status': 'Motion', 'activityTs': 1569078085275}

    @staticmethod
    def _get_my_zone(site: ADTPulseSite, zone_id: int) -> ADTPulseZoneData:
        if site.zones_as_dict is None:
            raise RuntimeError("ADT pulse returned null zone")
        return site.zones_as_dict[zone_id]

    @staticmethod
    def _determine_device_class(zone_data: ADTPulseZoneData) -> BinarySensorDeviceClass:
        # map the ADT Pulse device type tag to a binary_sensor class
        # so the proper status codes and icons are displayed. If device class
        # is not specified, binary_sensor defaults to a generic on/off sensor

        tags = zone_data.tags
        device_class: Optional[BinarySensorDeviceClass] = None
        if "sensor" in tags:
            for tag in tags:
                try:
                    device_class = ADT_DEVICE_CLASS_TAG_MAP[tag]
                    break
                except KeyError:
                    continue
        # since ADT Pulse does not separate the concept of a door or window sensor,
        # we try to autodetect window type sensors so the appropriate icon is displayed
        if device_class is None:
            LOG.warn(
                "Ignoring unsupported sensor type from ADT Pulse cloud service "
                f"configured tags: {tags}"
            )
            raise ValueError(f"Unknown ADT Pulse device class {device_class}")
        if device_class == BinarySensorDeviceClass.DOOR:
            if "Window" in zone_data.name or "window" in zone_data.name:
                device_class = BinarySensorDeviceClass.WINDOW
        LOG.info(
            f"Determined {zone_data.name} device class {device_class} "
            f"from ADT Pulse service configured tags {tags}"
        )
        return device_class

    def __init__(
        self,
        coordinator: ADTPulseDataUpdateCoordinator,
        site: ADTPulseSite,
        zone_id: int,
    ):
        """Initialize the binary_sensor."""
        LOG.debug(f"{ADTPULSE_DOMAIN}: adding zone sensor for site {site.id}")
        self._site = site
        self._zone_id = zone_id
        self._my_zone = self._get_my_zone(site, zone_id)
        self._device_class = self._determine_device_class(self._my_zone)
        super().__init__(coordinator, self._my_zone.name)
        LOG.debug(f"Created ADT Pulse '{self._device_class}' sensor '{self.name}'")

    @property
    def name(self) -> str:
        """Return the name of the zone."""
        return self._my_zone.name

    @property
    def id(self) -> str:
        """Return the id of the ADT sensor."""
        return self._my_zone.id_

    @property
    def unique_id(self) -> str:
        """Return HA unique id."""
        return f"adt_pulse_sensor_{self._site.id}_{self._zone_id}"

    @property
    def icon(self) -> str:
        """Get icon.

        Returns:
            str: returns mdi:icon corresponding to current state
        """
        if self.device_class not in ADT_SENSOR_ICON_MAP:
            LOG.error(
                f"Unknown ADT Pulse binary sensor device type {self.device_class}"
            )
            return "mdi:alert-octogram"
        if self.is_on:
            return ADT_SENSOR_ICON_MAP[self.device_class][0]
        return ADT_SENSOR_ICON_MAP[self.device_class][1]

    @property
    def is_on(self) -> bool:
        """Return True if the binary sensor is on."""
        # sensor is considered tripped if the state is anything but OK
        return not self._my_zone.state == STATE_OK

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        """Return the class of the binary sensor."""
        return self._device_class

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return extra state attributes.

        currently status and last_activity_timestamp
        """
        return {
            "status": self._my_zone.status,
            "last_activity_timestamp": self._my_zone.last_activity_timestamp,
        }

    @property
    def attribution(self) -> str:
        """Return API data attribution."""
        return ADTPULSE_DATA_ATTRIBUTION

    @callback
    def _handle_coordinator_update(self) -> None:
        LOG.debug(
            f"Setting ADT Pulse zone {self.id} to {self.is_on} "
            f"at timestamp {self._my_zone.last_activity_timestamp}"
        )
        self.async_write_ha_state()


class ADTPulseGatewaySensor(
    CoordinatorEntity[ADTPulseDataUpdateCoordinator], BinarySensorEntity
):
    """HASS Gateway Online Binary Sensor."""

    def __init__(self, coordinator: ADTPulseDataUpdateCoordinator, service: PyADTPulse):
        """Initialize gateway sensor.

        Args:
            coordinator (ADTPulseDataUpdateCoordinator):
                HASS data update coordinator
            service (PyADTPulse): API Pulse connection object
        """
        LOG.debug(
            f"{ADTPULSE_DOMAIN}: adding gateway status sensor for site "
            f"{service.sites[0].name}"
        )
        self._service = service
        self._device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._name = f"{self._service.sites[0].name} Pulse Gateway Status"
        super().__init__(coordinator, self._name)

    @property
    def is_on(self) -> bool:
        """Return if gateway is online."""
        return self._service.gateway_online

    @property
    def name(self) -> str:
        return self._name

    # FIXME: Gateways only support one site?
    @property
    def unique_id(self) -> str:
        """Return HA unique id."""
        return f"adt_pulse_gateway_{self._service.sites[0].id}"

    @property
    def icon(self) -> str:
        if self.is_on:
            return "mdi:lan-connect"
        return "mdi:lan-disconnect"

    @property
    def attribution(self) -> str | None:
        """Return API data attribution."""
        return ADTPULSE_DATA_ATTRIBUTION

    @callback
    def _handle_coordinator_update(self) -> None:
        LOG.debug(f"Setting Pulse Gateway status to {self._service.gateway_online}")
        self.async_write_ha_state()
