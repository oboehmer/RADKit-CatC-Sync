"""Build RADKit device creation/update models from CatC data."""

from __future__ import annotations

from typing import Any

from radkit_common.types import (
    ConnectionMethod,
    CustomSecretStr,
    DeviceType,
)
from radkit_service.webserver.connectors.utils import dict_to_metadata  # type: ignore[attr-defined]
from radkit_service.webserver.models.devices import (
    MetaDataEntry,
    NewDevice,
    NewTerminal,
    UpdateDevice,
    UpdateMetaDataSet,
    UpdateTerminal,
)

from .models import CatCDevice

# Device type mapping (mirrors the logic in radkit_service connector)
_SOFTWARE_TYPE_MAP: dict[str, DeviceType] = {
    "NXOS": DeviceType.NX_OS,
    "IOS": DeviceType.IOS_XE,
    "IOS-XE": DeviceType.IOS_XE,
}
_SERIES_TYPE_MAP: dict[str, DeviceType] = {
    "cisco catalyst 9800 series wireless controllers": DeviceType.WLC,
}


def get_device_type(software_type: str | None, series: str | None) -> DeviceType:
    """Map CatC softwareType / series fields to a RADKit DeviceType."""
    if series is not None:
        matched = _SERIES_TYPE_MAP.get(series.lower())
        if matched is not None:
            return matched
    if software_type is None:
        return DeviceType.GENERIC
    return _SOFTWARE_TYPE_MAP.get(software_type, DeviceType.GENERIC)


def build_metadata(
    device: CatCDevice,
    catc_hostname: str,
    metadata_fields: frozenset[str],
    meta_source_key: str,
) -> list[MetaDataEntry]:
    """
    Build RADKit metadata from selected CatC inventory fields plus the source ownership marker.

    Args:
        device: CatC device data.
        catc_hostname: Source CatC cluster hostname.
        metadata_fields: Set of CatC field names to include in metadata.
        meta_source_key: Key name for the source ownership marker.

    Returns:
        List of MetaDataEntry objects.
    """
    raw = {
        k: str(v) if v is not None else "" for k, v in device.raw.items() if k in metadata_fields
    }

    meta: list[MetaDataEntry] = list(dict_to_metadata(raw))

    # Upsert meta_source_key
    existing_keys = {m.key for m in meta}
    if meta_source_key in existing_keys:
        meta = [
            MetaDataEntry(key=meta_source_key, value=catc_hostname)
            if m.key == meta_source_key
            else m
            for m in meta
        ]
    else:
        meta.append(MetaDataEntry(key=meta_source_key, value=catc_hostname))

    return meta


def build_new_device(
    device: CatCDevice,
    catc_hostname: str,
    ssh_user: str,
    ssh_password: str,
    radkit_name: str,
    metadata_fields: frozenset[str],
    meta_source_key: str,
) -> NewDevice:
    """Build a NewDevice model for adding to RADKit."""
    terminal = NewTerminal(
        connection_method=ConnectionMethod.SSH,
        port=22,
        username=ssh_user,
        password=CustomSecretStr(ssh_password),
    )
    return NewDevice(
        name=radkit_name,
        host=device.management_ip,
        device_type=get_device_type(
            software_type=device.software_type,
            series=device.series,
        ),
        description=f"Imported from CatC: {catc_hostname}",
        meta_data=build_metadata(device, catc_hostname, metadata_fields, meta_source_key),
        enabled=True,
        terminal=terminal,
    )


def build_update_device(
    device: CatCDevice,
    catc_hostname: str,
    existing_uuid: str,
    update_passwords: bool,
    ssh_user: str,
    ssh_password: str,
    metadata_fields: frozenset[str],
    meta_source_key: str,
) -> UpdateDevice:
    """Build an UpdateDevice model for updating in RADKit."""
    meta_update = UpdateMetaDataSet(
        replace=build_metadata(device, catc_hostname, metadata_fields, meta_source_key)
    )

    kwargs: dict[str, Any] = {
        "uuid": existing_uuid,
        "host": device.management_ip,
        "device_type": get_device_type(
            software_type=device.software_type,
            series=device.series,
        ),
        "description": f"Imported from CatC: {catc_hostname}",
        "meta_data_update": meta_update,
    }

    if update_passwords:
        kwargs["terminal"] = UpdateTerminal(
            username=ssh_user,
            password=CustomSecretStr(ssh_password),
        )

    return UpdateDevice(**kwargs)
