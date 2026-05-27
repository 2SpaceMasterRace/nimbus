from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="DeleteResultResponse")


@_attrs_define
class DeleteResultResponse:
    """JSON response model for delete operations.

    Attributes:
        deleted (bool):
        version_id (None | str | Unset):
        request_charged (bool | None | Unset):
    """

    deleted: bool
    version_id: None | str | Unset = UNSET
    request_charged: bool | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        deleted = self.deleted

        version_id: None | str | Unset
        if isinstance(self.version_id, Unset):
            version_id = UNSET
        else:
            version_id = self.version_id

        request_charged: bool | None | Unset
        if isinstance(self.request_charged, Unset):
            request_charged = UNSET
        else:
            request_charged = self.request_charged

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "deleted": deleted,
            }
        )
        if version_id is not UNSET:
            field_dict["version_id"] = version_id
        if request_charged is not UNSET:
            field_dict["request_charged"] = request_charged

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        deleted = d.pop("deleted")

        def _parse_version_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        version_id = _parse_version_id(d.pop("version_id", UNSET))

        def _parse_request_charged(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        request_charged = _parse_request_charged(d.pop("request_charged", UNSET))

        delete_result_response = cls(
            deleted=deleted,
            version_id=version_id,
            request_charged=request_charged,
        )

        delete_result_response.additional_properties = d
        return delete_result_response

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
