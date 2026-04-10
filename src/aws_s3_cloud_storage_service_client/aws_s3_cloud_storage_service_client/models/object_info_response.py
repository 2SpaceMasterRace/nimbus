from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.object_info_response_metadata_type_0 import ObjectInfoResponseMetadataType0


T = TypeVar("T", bound="ObjectInfoResponse")


@_attrs_define
class ObjectInfoResponse:
    """JSON response model for object metadata.

    Attributes:
        object_name (str):
        version_id (None | str | Unset):
        data_type (None | str | Unset):
        integrity (None | str | Unset):
        encryption (None | str | Unset):
        storage_tier (None | str | Unset):
        size_bytes (int | None | Unset):
        updated_at (datetime.datetime | None | Unset):
        metadata (None | ObjectInfoResponseMetadataType0 | Unset):
    """

    object_name: str
    version_id: None | str | Unset = UNSET
    data_type: None | str | Unset = UNSET
    integrity: None | str | Unset = UNSET
    encryption: None | str | Unset = UNSET
    storage_tier: None | str | Unset = UNSET
    size_bytes: int | None | Unset = UNSET
    updated_at: datetime.datetime | None | Unset = UNSET
    metadata: None | ObjectInfoResponseMetadataType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.object_info_response_metadata_type_0 import ObjectInfoResponseMetadataType0

        object_name = self.object_name

        version_id: None | str | Unset
        if isinstance(self.version_id, Unset):
            version_id = UNSET
        else:
            version_id = self.version_id

        data_type: None | str | Unset
        if isinstance(self.data_type, Unset):
            data_type = UNSET
        else:
            data_type = self.data_type

        integrity: None | str | Unset
        if isinstance(self.integrity, Unset):
            integrity = UNSET
        else:
            integrity = self.integrity

        encryption: None | str | Unset
        if isinstance(self.encryption, Unset):
            encryption = UNSET
        else:
            encryption = self.encryption

        storage_tier: None | str | Unset
        if isinstance(self.storage_tier, Unset):
            storage_tier = UNSET
        else:
            storage_tier = self.storage_tier

        size_bytes: int | None | Unset
        if isinstance(self.size_bytes, Unset):
            size_bytes = UNSET
        else:
            size_bytes = self.size_bytes

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        elif isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, ObjectInfoResponseMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "object_name": object_name,
            }
        )
        if version_id is not UNSET:
            field_dict["version_id"] = version_id
        if data_type is not UNSET:
            field_dict["data_type"] = data_type
        if integrity is not UNSET:
            field_dict["integrity"] = integrity
        if encryption is not UNSET:
            field_dict["encryption"] = encryption
        if storage_tier is not UNSET:
            field_dict["storage_tier"] = storage_tier
        if size_bytes is not UNSET:
            field_dict["size_bytes"] = size_bytes
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.object_info_response_metadata_type_0 import ObjectInfoResponseMetadataType0

        d = dict(src_dict)
        object_name = d.pop("object_name")

        def _parse_version_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        version_id = _parse_version_id(d.pop("version_id", UNSET))

        def _parse_data_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        data_type = _parse_data_type(d.pop("data_type", UNSET))

        def _parse_integrity(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        integrity = _parse_integrity(d.pop("integrity", UNSET))

        def _parse_encryption(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        encryption = _parse_encryption(d.pop("encryption", UNSET))

        def _parse_storage_tier(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        storage_tier = _parse_storage_tier(d.pop("storage_tier", UNSET))

        def _parse_size_bytes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        size_bytes = _parse_size_bytes(d.pop("size_bytes", UNSET))

        def _parse_updated_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = isoparse(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        def _parse_metadata(data: object) -> None | ObjectInfoResponseMetadataType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = ObjectInfoResponseMetadataType0.from_dict(data)

                return metadata_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ObjectInfoResponseMetadataType0 | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        object_info_response = cls(
            object_name=object_name,
            version_id=version_id,
            data_type=data_type,
            integrity=integrity,
            encryption=encryption,
            storage_tier=storage_tier,
            size_bytes=size_bytes,
            updated_at=updated_at,
            metadata=metadata,
        )

        object_info_response.additional_properties = d
        return object_info_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
