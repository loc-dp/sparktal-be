"""OpenAPI tweaks for the companies API. Imported in CompaniesConfig.ready()."""

from typing import Any

from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.openapi import AutoSchema


# drf-spectacular's extension API is untyped.
class OfficeSchema(OpenApiSerializerExtension):  # type: ignore[no-untyped-call]
    """An office comes back with its id; a new one is sent without, so id is optional only
    on the way in."""

    target_class = "apps.companies.serializers.OfficeSerializer"

    def map_serializer(self, auto_schema: AutoSchema, direction: Any) -> Any:
        schema = auto_schema._map_serializer(self.target, direction, bypass_extensions=True)  # type: ignore[no-untyped-call]
        if direction == "response":
            schema["required"] = sorted({*schema.get("required", []), "id"})
        return schema
